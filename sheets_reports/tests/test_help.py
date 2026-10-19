"""El centro de ayuda (sheets_reports/help.py + templates/sheets_reports/help/): cada tópico tiene
su partial y ningún enlace (`#help:<slug>`) ni botón (`help.open('<slug>')`) apunta a uno que
no existe."""
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase

from sheets_reports.help import HELP_TOPICS, HELP_VIDEOS, SECTIONS, VIDEO_DIR, help_index, help_sections
from sheets_reports.tests.fixtures import make_board

PACKAGE = Path(__file__).resolve().parents[1]
LINK = re.compile(r"""#help:([\w-]+)""")
OPEN = re.compile(r"""help\.open\(\s*['"]([\w-]+)['"]""")


class HelpRegistryTests(SimpleTestCase):
    def test_cada_topico_tiene_su_partial_y_su_seccion(self):
        sections = {key for key, _, _ in SECTIONS}
        for topic in help_index():
            with self.subTest(topic=topic["slug"]):
                get_template(topic["template"])
                self.assertIn(topic["section"], sections)

    def test_los_slugs_no_se_repiten(self):
        slugs = [slug for slug, *_ in HELP_TOPICS]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_las_secciones_tienen_topicos_en_orden(self):
        """El índice sigue el orden de SECTIONS y cada sección tiene al menos un tópico."""
        grouped = help_sections()
        self.assertEqual([s["key"] for s in grouped], [key for key, _, _ in SECTIONS])
        for section in grouped:
            self.assertTrue(section["topics"], section["key"])

    def test_ningun_enlace_apunta_a_un_topico_que_no_existe(self):
        slugs = {slug for slug, *_ in HELP_TOPICS}
        files = [*(PACKAGE / "templates").rglob("*.html"), *(PACKAGE / "static").rglob("*.js")]
        broken = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for slug in LINK.findall(text) + OPEN.findall(text):
                if slug not in slugs:
                    broken.append(f"{path.relative_to(PACKAGE)}: {slug}")
        self.assertEqual(broken, [])


class HelpPanelTests(TestCase):
    def test_el_editor_tiene_la_ayuda(self):
        user = get_user_model().objects.create_user("u", password="x")
        self.client.force_login(user)
        dashboard, _ = make_board(user)
        r = self.client.get(f"/tableros/{dashboard.id}/edit/")
        self.assertContains(r, 'id="help-panel"')
        self.assertContains(r, "$store.help.open()")      # el ítem «Ayuda» del menú del tablero
        self.assertContains(r, "help-panel.js")
        for slug, *_ in HELP_TOPICS:
            self.assertContains(r, f'id="help-topic-{slug}"')

    def test_la_vista_compartida_no_la_tiene(self):
        user = get_user_model().objects.create_user("u", password="x")
        self.client.force_login(user)
        dashboard, _ = make_board(user)
        self.assertNotContains(self.client.get(f"/tableros/{dashboard.id}/view/"), 'id="help-panel"')


class HelpVideoTests(SimpleTestCase):
    def test_cada_video_tiene_su_topico_su_archivo_y_su_portada(self):
        slugs = {slug for slug, *_ in HELP_TOPICS}
        static_dir = PACKAGE / "static" / VIDEO_DIR
        for slug, (name, length) in HELP_VIDEOS.items():
            with self.subTest(topic=slug):
                self.assertIn(slug, slugs)
                self.assertTrue((static_dir / f"{name}.webm").is_file(), name)
                self.assertTrue((static_dir / f"{name}.png").is_file(), name)
                self.assertTrue(length)
