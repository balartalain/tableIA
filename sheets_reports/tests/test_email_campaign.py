"""Envío de correos de un tablero: leer el CSV de destinatarios, cuándo le toca ejecutarse,
la ejecución (tope por remitente, reparto entre remitentes, reintentos, bitácora), sus
endpoints y el comando que llama el crontab."""
import json
import smtplib
from datetime import date, datetime, time, timezone
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from sheets_reports.models import EmailCampaign, EmailLog, EmailRecipient, EmailSender
from sheets_reports.services import mailing
from sheets_reports.tests.fixtures import make_board

LOCMEM = override_settings(CAMPAIGN_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                           CAMPAIGN_TIME_ZONE="America/Santo_Domingo")


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    """Un instante de junio de 2026 en UTC (Santo Domingo va 4 horas detrás). El 1 es lunes."""
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc)


def make_campaign(dashboard, recipients=3, senders=("a@gmail.com",), **fields) -> EmailCampaign:
    campaign = EmailCampaign.objects.create(dashboard=dashboard, **{
        "enabled": True, "subject": "Hola", "body": "Mensaje", "per_sender_limit": 100,
        "send_time": time(8, 0), **fields})
    for email in senders:
        EmailSender.objects.create(campaign=campaign, email=email,
                                   password_encrypted=mailing.encrypt_password("clave"))
    EmailRecipient.objects.bulk_create(
        [EmailRecipient(campaign=campaign, email=f"p{i}@uapa.edu.do") for i in range(recipients)])
    return campaign


class RecipientsCsvTests(SimpleTestCase):
    def test_con_encabezado_repetidos_e_invalidos(self):
        content = "Correo\nAna@uapa.edu.do\nana@uapa.edu.do\nno-es-correo\n\nluis@uapa.edu.do\n".encode()
        valid, invalid = mailing.parse_recipients_csv(content)
        self.assertEqual(valid, ["ana@uapa.edu.do", "luis@uapa.edu.do"])
        self.assertEqual(invalid, ["no-es-correo"])

    def test_sin_encabezado_y_con_otras_columnas(self):
        content = "Ana Pérez;ana@uapa.edu.do\nLuis;luis@uapa.edu.do\n".encode("latin-1")
        valid, invalid = mailing.parse_recipients_csv(content)
        self.assertEqual(valid, ["ana@uapa.edu.do", "luis@uapa.edu.do"])
        self.assertEqual(invalid, [])


class PasswordTests(SimpleTestCase):
    def test_se_guarda_cifrada_y_se_recupera(self):
        token = mailing.encrypt_password("abcd efgh")
        self.assertNotIn("abcd", token)
        self.assertEqual(mailing.decrypt_password(token), "abcd efgh")

    def test_una_ilegible_avisa(self):
        with self.assertRaises(mailing.MailingError):
            mailing.decrypt_password("no-es-un-token")


class MessageTests(SimpleTestCase):
    def test_el_html_se_vuelve_texto_plano(self):
        html = "<p>Hola <strong>mundo</strong></p><ul><li>uno</li><li>dos</li></ul>"
        self.assertEqual(mailing.html_to_text(html), "Hola mundo\nuno\ndos")

    def test_sin_html_no_cambia(self):
        self.assertEqual(mailing.html_to_text(""), "")
        self.assertEqual(mailing.html_to_text("Texto plano"), "Texto plano")

    def _sent(self, body):
        campaign = EmailCampaign(subject="Hola", body=body)
        msg = mailing._message(campaign, "a@gmail.com", ["x@uapa.edu.do"], None).message()
        parts = list(msg.walk())
        html = next(p for p in parts if p.get_content_type() == "text/html").get_payload(decode=True).decode()
        images = [p for p in parts if p.get_content_maintype() == "image"]
        return msg, html, images

    def test_las_imagenes_van_en_linea_y_no_en_base64(self):
        # Gmail no muestra una imagen `data:` en un correo recibido; adjunta en línea (cid), sí.
        png = "iVBORw0KGgo="
        msg, html, images = self._sent(f'<p>Hola</p><img src="data:image/png;base64,{png}">'
                                       f'<img src="data:image/png;base64,{png}">')
        self.assertEqual(msg.get_content_type(), "multipart/related")
        self.assertNotIn("data:image", html)
        self.assertEqual(html.count('src="cid:img1@dataviz"'), 2)
        self.assertEqual(len(images), 1)  # la misma imagen, una sola vez
        self.assertEqual(images[0].get_content_type(), "image/png")
        self.assertEqual(images[0]["Content-ID"], "<img1@dataviz>")
        self.assertTrue(images[0]["Content-Disposition"].startswith("inline"))

    def test_sin_imagenes_no_hay_adjuntos(self):
        msg, html, images = self._sent("<p>Hola</p>")
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertEqual((html, images), ("<p>Hola</p>", []))


class PacerTests(SimpleTestCase):
    def test_no_espera_antes_del_primero_y_espacia_los_demas(self):
        with mock.patch("sheets_reports.services.mailing.sleep") as sleep:
            pacer = mailing._Pacer(interval=1.0)
            for _ in range(3):
                pacer.before_send()
        self.assertEqual(sleep.call_args_list, [mock.call(1.0), mock.call(1.0)])

    def test_renueva_el_candado_cada_tantos_envios(self):
        renewals = []
        with mock.patch("sheets_reports.services.mailing.sleep"):
            pacer = mailing._Pacer(keepalive=lambda: renewals.append(1), keepalive_every=2, interval=0)
            for _ in range(4):
                pacer.before_send()
        self.assertEqual(len(renewals), 2)


@LOCMEM
class ScheduleTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.dashboard, _ = make_board(user)

    def test_diario_despues_de_la_hora_local(self):
        campaign = make_campaign(self.dashboard)
        self.assertFalse(mailing.is_due(campaign, utc(1, 11, 59)))  # 07:59 en Santo Domingo
        self.assertTrue(mailing.is_due(campaign, utc(1, 12, 0)))    # 08:00
        self.assertTrue(mailing.is_due(campaign, utc(1, 20, 0)))    # un cron atrasado

    def test_con_el_dia_cerrado_no_le_toca(self):
        campaign = make_campaign(self.dashboard, last_run_on=date(2026, 6, 1))
        self.assertFalse(mailing.is_due(campaign, utc(1, 15)))
        self.assertTrue(mailing.is_due(campaign, utc(2, 15)))

    def test_con_el_maximo_del_dia_enviado_no_le_toca(self):
        campaign = make_campaign(self.dashboard, recipients=3)
        campaign.recipients.filter(email__in=["p0@uapa.edu.do", "p1@uapa.edu.do"]).update(
            status=EmailRecipient.SENT, sent_at=utc(1, 14))
        self.assertTrue(mailing.is_due(campaign, utc(1, 15)))
        # Un envío que se cortó a medias se retoma ya mismo.
        self.assertEqual(mailing.next_run(campaign, utc(1, 15)), mailing.local_now(utc(1, 15)))
        campaign.per_sender_limit = 2
        self.assertFalse(mailing.is_due(campaign, utc(1, 15)))
        self.assertEqual(mailing.next_run(campaign, utc(1, 15)).date(), date(2026, 6, 2))

    def test_semanal_solo_sus_dias(self):
        campaign = make_campaign(self.dashboard, frequency=EmailCampaign.WEEKLY, weekdays=[2])
        self.assertFalse(mailing.is_due(campaign, utc(1, 15)))  # lunes
        self.assertTrue(mailing.is_due(campaign, utc(3, 15)))   # miércoles
        self.assertEqual(mailing.next_run(campaign, utc(1, 15)).date(), date(2026, 6, 3))

    def test_desactivada_o_sin_pendientes_no_le_toca(self):
        self.assertFalse(mailing.is_due(make_campaign(self.dashboard, enabled=False), utc(1, 15)))
        EmailCampaign.objects.all().delete()
        campaign = make_campaign(self.dashboard, recipients=0)
        self.assertFalse(mailing.is_due(campaign, utc(1, 15)))
        self.assertIsNone(mailing.next_run(campaign, utc(1, 15)))


@LOCMEM
class RunCampaignTests(TestCase):
    def setUp(self):
        patcher = mock.patch("sheets_reports.services.mailing.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.dashboard, _ = make_board(user)

    def statuses(self, campaign) -> list[str]:
        return list(campaign.recipients.order_by("id").values_list("status", flat=True))

    def test_envia_a_los_pendientes_y_los_marca(self):
        campaign = make_campaign(self.dashboard)
        result = mailing.run_campaign(campaign, utc(1, 15))
        self.assertEqual(result, {"sent": 3, "failed": 0, "pending": 0})
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual((mail.outbox[0].subject, mail.outbox[0].body, mail.outbox[0].from_email),
                         ("Hola", "Mensaje", "a@gmail.com"))
        self.assertEqual(self.statuses(campaign), ["sent"] * 3)
        self.assertIn("Envío completado", campaign.logs.first().message)

    def test_el_mensaje_va_en_html_con_su_alternativa_en_texto(self):
        campaign = make_campaign(self.dashboard, recipients=1,
                                 body="<p>Hola <strong>mundo</strong></p>")
        mailing.run_campaign(campaign, utc(1, 15))
        self.assertEqual(mail.outbox[0].body, "Hola mundo")
        self.assertEqual(mail.outbox[0].alternatives,
                         [("<p>Hola <strong>mundo</strong></p>", "text/html")])

    def test_espacia_los_correos_uno_por_segundo(self):
        campaign = make_campaign(self.dashboard, recipients=3)
        mailing.run_campaign(campaign, utc(1, 15))
        self.assertEqual(self.sleep.call_args_list, [mock.call(1.0), mock.call(1.0)])

    def test_respeta_el_tope_por_remitente(self):
        campaign = make_campaign(self.dashboard, recipients=5, per_sender_limit=2)
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 15))["sent"], 2)
        # El mismo día ya no hay cupo, ni forzando; al día siguiente siguen los pendientes.
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 16), force=True)["sent"], 0)
        self.assertEqual(mailing.run_campaign(campaign, utc(2, 15))["sent"], 2)
        self.assertEqual(self.statuses(campaign), ["sent"] * 4 + ["pending"])

    def test_con_el_maximo_alcanzado_no_envia_mas_ese_dia(self):
        campaign = make_campaign(self.dashboard, recipients=4, per_sender_limit=2)
        mailing.run_campaign(campaign, utc(1, 15))
        self.assertIsNone(mailing.run_campaign(EmailCampaign.objects.get(id=campaign.id), utc(1, 15, 5)))
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Se alcanzó el máximo", campaign.logs.first().message)

    def test_el_reparto_no_rebasa_el_tope_de_cada_remitente(self):
        campaign = make_campaign(self.dashboard, recipients=7, per_sender_limit=2,
                                 senders=("a@gmail.com", "b@gmail.com"))
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 15)), {"sent": 4, "failed": 0, "pending": 3})
        # Dos por remitente en ronda: 4 correos espaciados 1 s (el primero no espera).
        self.assertEqual(self.sleep.call_args_list, [mock.call(1.0)] * 3)
        used = sorted(campaign.recipients.filter(status=EmailRecipient.SENT).values_list("sender_email", flat=True))
        self.assertEqual(used, ["a@gmail.com", "a@gmail.com", "b@gmail.com", "b@gmail.com"])
        self.assertFalse(mailing.is_due(campaign, utc(1, 15, 5)))

    def test_el_envio_para_en_el_tope_del_dia(self):
        campaign = make_campaign(self.dashboard, recipients=5, per_sender_limit=4)
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 15)), {"sent": 4, "failed": 0, "pending": 1})
        self.assertEqual(self.sleep.call_count, 3)  # 4 correos espaciados 1 s: el primero no espera
        self.assertEqual(mailing.next_run(campaign, utc(1, 15, 5)).date(), date(2026, 6, 2))

    def test_el_cupo_del_dia_crece_con_los_remitentes(self):
        campaign = make_campaign(self.dashboard, recipients=6, per_sender_limit=2,
                                 senders=("a@gmail.com", "b@gmail.com"))
        self.assertEqual(mailing._capacity(campaign), 4)
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 15))["sent"], 4)

    def test_un_remitente_que_ya_gasto_su_cupo_hoy_no_vuelve_a_enviar(self):
        campaign = make_campaign(self.dashboard, recipients=3, per_sender_limit=1,
                                 senders=("a@gmail.com", "b@gmail.com"))
        # «a» ya gastó su único correo de hoy: solo queda cupo para «b».
        EmailRecipient.objects.filter(email="p0@uapa.edu.do").update(
            status=EmailRecipient.SENT, sent_at=utc(1, 14), sender_email="a@gmail.com")
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 15)), {"sent": 1, "failed": 0, "pending": 1})
        self.assertEqual(campaign.recipients.get(email="p1@uapa.edu.do").sender_email, "b@gmail.com")

    def test_el_que_falla_no_se_reintenta_en_la_misma_ejecucion(self):
        campaign = make_campaign(self.dashboard, recipients=3)
        real_send = mailing.EmailMultiAlternatives.send

        def send(message, *args, **kwargs):
            if message.to == ["p0@uapa.edu.do"]:
                raise smtplib.SMTPRecipientsRefused({})
            return real_send(message, *args, **kwargs)

        with mock.patch("sheets_reports.services.mailing.EmailMultiAlternatives.send", send):
            result = mailing.run_campaign(campaign, utc(1, 15))
        self.assertEqual(result, {"sent": 2, "failed": 1, "pending": 1})
        self.assertEqual(campaign.recipients.get(email="p0@uapa.edu.do").attempts, 1)

    def test_un_remitente_que_falla_sale_y_el_otro_termina(self):
        campaign = make_campaign(self.dashboard, recipients=4,
                                 senders=("a@gmail.com", "b@gmail.com"))
        real_connection = mailing._connection

        def connection(sender):
            if sender.email == "a@gmail.com":
                raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
            return real_connection(sender)

        with mock.patch("sheets_reports.services.mailing._connection", connection):
            result = mailing.run_campaign(campaign, utc(1, 15))
        self.assertEqual(result, {"sent": 4, "failed": 0, "pending": 0})
        self.assertEqual(set(campaign.recipients.values_list("sender_email", flat=True)), {"b@gmail.com"})
        self.assertEqual(campaign.logs.filter(level=EmailLog.ERROR).count(), 1)

    def test_con_otro_envio_en_curso_no_envia(self):
        campaign = make_campaign(self.dashboard, running_since=utc(1, 14, 58))
        self.assertIsNone(mailing.run_campaign(campaign, utc(1, 15)))
        self.assertEqual(len(mail.outbox), 0)
        # Un candado viejo es de un envío que murió: no bloquea.
        self.assertEqual(mailing.run_campaign(campaign, utc(1, 15, 9))["sent"], 3)
        self.assertIsNone(EmailCampaign.objects.get(id=campaign.id).running_since)

    def test_sin_remitentes_tambien_cierra_el_dia(self):
        campaign = make_campaign(self.dashboard, senders=())
        mailing.run_campaign(campaign, utc(1, 15))
        self.assertFalse(mailing.is_due(campaign, utc(1, 15, 5)))
        self.assertTrue(mailing.is_due(campaign, utc(2, 15)))

    def test_reparte_entre_los_remitentes(self):
        campaign = make_campaign(self.dashboard, recipients=4, senders=("a@gmail.com", "b@gmail.com"))
        mailing.run_campaign(campaign, utc(1, 15))
        used = sorted(campaign.recipients.values_list("sender_email", flat=True))
        self.assertEqual(used, ["a@gmail.com", "a@gmail.com", "b@gmail.com", "b@gmail.com"])

    def test_un_remitente_que_no_inicia_sesion_no_pierde_sus_destinatarios(self):
        campaign = make_campaign(self.dashboard, recipients=2)
        error = smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        with mock.patch("sheets_reports.services.mailing._connection", side_effect=error):
            result = mailing.run_campaign(campaign, utc(1, 15))
        self.assertEqual(result, {"sent": 0, "failed": 0, "pending": 2})
        self.assertEqual(list(campaign.recipients.values_list("attempts", flat=True)), [0, 0])
        log = campaign.logs.filter(level=EmailLog.ERROR).get()
        self.assertIn("No se pudo iniciar sesión", log.message)
        self.assertEqual(log.sender_email, "a@gmail.com")

    def test_un_destinatario_que_falla_se_reintenta_hasta_tres_veces(self):
        campaign = make_campaign(self.dashboard, recipients=1)
        with mock.patch("sheets_reports.services.mailing.EmailMultiAlternatives.send",
                        side_effect=smtplib.SMTPRecipientsRefused({})):
            for day in (1, 2, 3):
                mailing.run_campaign(campaign, utc(day, 15))
        recipient = campaign.recipients.get()
        self.assertEqual((recipient.status, recipient.attempts), (EmailRecipient.FAILED, 3))
        self.assertEqual(campaign.logs.filter(level=EmailLog.ERROR, recipient_email=recipient.email).count(), 3)
        self.assertFalse(mailing.is_due(campaign, utc(4, 15)))

    def test_sin_remitentes_lo_dice_en_la_bitacora(self):
        campaign = make_campaign(self.dashboard, senders=())
        mailing.run_campaign(campaign, utc(1, 15))
        self.assertIn("No hay remitentes", campaign.logs.get().message)

    def test_comando_del_crontab(self):
        make_campaign(self.dashboard, send_time=time(23, 59), last_run_on=None)
        out = StringIO()
        with mock.patch("sheets_reports.services.mailing.local_now",
                        return_value=mailing.local_now(utc(1, 15))):
            call_command("send_campaign_emails", stdout=out)
            self.assertEqual(len(mail.outbox), 0)  # todavía no es la hora
            call_command("send_campaign_emails", dashboard=self.dashboard.id, force=True, stdout=out)
            call_command("send_campaign_emails", dashboard=self.dashboard.id + 1, force=True, stdout=out)
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("3 enviados", out.getvalue())
        self.assertIn("no tiene envío de correos", out.getvalue())


@LOCMEM
class CampaignEndpointsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, _ = make_board(self.user)
        self.url = f"/api/dashboard/{self.dashboard.id}/email-campaign/"

    def put(self, **changes):
        body = {"enabled": False, "subject": "Hola", "body": "Mensaje", "per_sender_limit": 50,
                "frequency": "daily", "send_time": "09:30", "weekdays": [],
                "senders": [{"email": "a@gmail.com", "password": "abcd efgh ijkl mnop"}], **changes}
        return self.client.put(self.url, json.dumps(body), content_type="application/json")

    def upload(self, text: str):
        return self.client.post(f"{self.url}recipients/",
                                {"file": SimpleUploadedFile("lista.csv", text.encode(), "text/csv")})

    def test_sin_configurar_devuelve_los_valores_por_defecto(self):
        data = self.client.get(self.url).json()
        self.assertEqual((data["enabled"], data["senders"], data["report"]["total"]), (False, [], 0))
        self.assertFalse(EmailCampaign.objects.exists())

    def test_la_franja_de_riesgo_del_cron_viene_en_la_config(self):
        self.assertEqual(self.client.get(self.url).json()["cron_minutes"], 5)
        with override_settings(CAMPAIGN_CRON_MINUTES=10):
            self.assertEqual(self.client.get(self.url).json()["cron_minutes"], 10)

    def test_la_contrasena_se_cifra_y_nunca_sale(self):
        r = self.put()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["senders"], [{"email": "a@gmail.com", "has_password": True}])
        self.assertNotIn("abcd", r.content.decode())
        self.assertNotIn("abcd", self.client.get(self.url).content.decode())
        sender = EmailSender.objects.get()
        # Google la muestra con espacios; se guarda sin ellos.
        self.assertEqual(mailing.decrypt_password(sender.password_encrypted), "abcdefghijklmnop")

    def test_sin_contrasena_nueva_conserva_la_guardada_y_quita_al_que_no_viene(self):
        self.put(senders=[{"email": "a@gmail.com", "password": "uno"}, {"email": "b@gmail.com", "password": "dos"}])
        r = self.put(senders=[{"email": "a@gmail.com", "password": ""}])
        self.assertEqual(r.status_code, 200, r.content)
        sender = EmailSender.objects.get()
        self.assertEqual((sender.email, mailing.decrypt_password(sender.password_encrypted)), ("a@gmail.com", "uno"))

    def test_guardar_reabre_el_dia(self):
        self.put()
        EmailCampaign.objects.update(last_run_on=date(2026, 6, 1))
        self.assertEqual(self.put(per_sender_limit=25).json()["per_sender_limit"], 25)
        self.assertIsNone(EmailCampaign.objects.get().last_run_on)

    def test_el_cuerpo_se_sanea_al_guardar(self):
        r = self.put(body='<p>Hola</p><script>alert(1)</script>'
                          '<img src="data:image/png;base64,AAA" onerror="alert(1)">'
                          '<a href="http://x.com">enlace</a>')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()["body"]
        self.assertNotIn("script", body)
        self.assertNotIn("onerror", body)
        self.assertIn('src="data:image/png;base64,AAA"', body)
        self.assertIn('target="_blank"', body)
        self.assertIn('rel="noopener noreferrer"', body)

    def test_un_cuerpo_sin_contenido_se_guarda_vacio(self):
        self.assertEqual(self.put(body="<p><br></p>").json()["body"], "")
        self.assertIn("cuerpo", self.put(enabled=True, body="<p><br></p>").json()["error"])

    def test_un_cuerpo_demasiado_grande_lo_avisa(self):
        with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=100):
            r = self.put(body="x" * 500)
        self.assertEqual(r.status_code, 400)
        self.assertIn("demasiado grande", r.json()["error"])

    def test_valida_lo_que_llega(self):
        for changes in ({"send_time": "25:00"}, {"per_sender_limit": 0}, {"per_sender_limit": 501},
                        {"frequency": "mensual"},
                        {"frequency": "weekly", "weekdays": []}, {"weekdays": [9]},
                        {"senders": [{"email": "sin-arroba", "password": "x"}]},
                        {"senders": [{"email": "nuevo@gmail.com", "password": ""}]}):
            with self.subTest(changes=changes):
                self.assertEqual(self.put(**changes).status_code, 400)

    def test_no_se_activa_sin_destinatarios_ni_sin_remitente(self):
        self.assertIn("destinatarios", self.put(enabled=True).json()["error"])
        self.upload("ana@uapa.edu.do\n")
        self.assertIn("remitente", self.put(enabled=True, senders=[]).json()["error"])
        self.assertTrue(self.put(enabled=True).json()["enabled"])

    def test_cargar_el_csv_agrega_sin_reiniciar_a_los_notificados(self):
        r = self.upload("Correo\nana@uapa.edu.do\nluis@uapa.edu.do\nmalo\n")
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual((data["added"], data["duplicates"], data["invalid"]), (2, 0, ["malo"]))
        EmailRecipient.objects.filter(email="ana@uapa.edu.do").update(status=EmailRecipient.SENT)
        data = self.upload("ana@uapa.edu.do\neva@uapa.edu.do\n").json()
        self.assertEqual((data["added"], data["duplicates"]), (1, 1))
        self.assertEqual(data["report"], {**data["report"], "total": 3, "sent": 1, "pending": 2})

    def test_un_csv_sin_correos_se_rechaza(self):
        self.assertEqual(self.upload("nombre\nAna\n").status_code, 400)

    def test_vaciar_la_lista_desactiva_el_envio(self):
        self.upload("ana@uapa.edu.do\n")
        self.put(enabled=True)
        data = self.client.delete(f"{self.url}recipients/").json()
        self.assertEqual((data["enabled"], data["report"]["total"]), (False, 0))

    def test_enviar_prueba(self):
        test_url = f"{self.url}test/"
        post = lambda to: self.client.post(test_url, json.dumps({"to": to}), content_type="application/json")
        self.assertEqual(post("yo@uapa.edu.do").status_code, 400)  # nada guardado todavía
        self.put()
        self.assertEqual(post("no-es-correo").status_code, 400)
        r = post("yo@uapa.edu.do")
        self.assertEqual(r.json(), {"sent": True, "sender": "a@gmail.com"})
        self.assertEqual(mail.outbox[0].to, ["yo@uapa.edu.do"])

    def test_solo_el_dueno_configura(self):
        other = get_user_model().objects.create_user("otro", "o@o.com", "x")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.put().status_code, 404)
        self.assertEqual(self.upload("ana@uapa.edu.do\n").status_code, 404)


@LOCMEM
class EmailReportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, _ = make_board(self.user)
        self.url = f"/api/dashboard/{self.dashboard.id}/email-report/"
        self.view_url = f"/tableros/{self.dashboard.id}/view/"

    def test_sin_envio_activado_no_hay_pestana_ni_reporte(self):
        self.assertNotContains(self.client.get(self.view_url), 'id="view-tabs"')
        self.assertEqual(self.client.get(self.url).status_code, 404)
        make_campaign(self.dashboard, enabled=False)
        self.assertNotContains(self.client.get(self.view_url), 'id="view-tabs"')
        self.assertEqual(self.client.get(f"{self.url}recipients/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}logs/").status_code, 404)

    def test_la_vista_compartida_muestra_la_pestana(self):
        make_campaign(self.dashboard)
        r = self.client.get(self.view_url)
        self.assertContains(r, 'id="view-tabs"')
        self.assertContains(r, "email-report.js")

    def test_los_totales_cambian_cuando_se_envia(self):
        campaign = make_campaign(self.dashboard, recipients=3, per_sender_limit=2)
        before = self.client.get(self.url).json()
        self.assertEqual((before["total"], before["sent"], before["pending"], before["last_log_id"]), (3, 0, 3, 0))
        mailing.run_campaign(campaign, utc(1, 15))
        after = self.client.get(self.url).json()
        self.assertEqual((after["sent"], after["pending"]), (2, 1))
        self.assertGreater(after["last_log_id"], before["last_log_id"])

    def test_el_estado_del_envio(self):
        campaign = make_campaign(self.dashboard, recipients=3, per_sender_limit=2, senders=())
        self.assertEqual(mailing.report_summary(campaign, utc(1, 11))["state"], "scheduled")
        campaign.running_since = utc(1, 14, 58)
        self.assertEqual(mailing.report_summary(campaign, utc(1, 15))["state"], "running")
        # Un candado viejo no es un envío en curso.
        self.assertEqual(mailing.report_summary(campaign, utc(1, 16))["state"], "scheduled")
        campaign.running_since = None
        mailing.run_campaign(campaign, utc(1, 15))  # sin remitentes: no envía y cierra el día
        stopped = self.client.get(self.url).json()
        self.assertEqual(mailing.report_summary(campaign, utc(1, 15, 5))["state"], "stopped")
        self.assertIn("No hay remitentes", stopped["last_error"]["message"])
        campaign.recipients.update(status=EmailRecipient.SENT, sent_at=utc(1, 15))
        self.assertEqual(mailing.report_summary(campaign, utc(1, 15, 5))["state"], "done")

    def test_la_fecha_estimada_de_fin(self):
        campaign = make_campaign(self.dashboard, recipients=5, per_sender_limit=2)
        self.assertEqual(mailing.report_summary(campaign, utc(1, 11))["estimated_end"], "2026-06-03")
        campaign.frequency, campaign.weekdays = EmailCampaign.WEEKLY, [2]  # solo los miércoles
        self.assertEqual(mailing.report_summary(campaign, utc(1, 11))["estimated_end"], "2026-06-17")
        campaign.recipients.update(status=EmailRecipient.SENT, sent_at=utc(1, 15))
        self.assertIsNone(mailing.report_summary(campaign, utc(1, 16))["estimated_end"])

    def test_enviados_por_dia_remitentes_y_errores_por_motivo(self):
        campaign = make_campaign(self.dashboard, recipients=5, senders=("a@gmail.com", "b@gmail.com"))
        first, second, third, fourth, _ = campaign.recipients.order_by("id")
        # Las 03:00 UTC del día 2 todavía son el día 1 en Santo Domingo.
        for recipient, sent_at in ((first, utc(1, 15)), (second, utc(2, 3)), (third, utc(2, 15))):
            EmailRecipient.objects.filter(id=recipient.id).update(
                status=EmailRecipient.SENT, sent_at=sent_at, sender_email="a@gmail.com")
        for attempt in (1, 2):
            EmailLog.objects.create(campaign=campaign, level=EmailLog.ERROR, recipient_email=fourth.email,
                                    sender_email="a@gmail.com",
                                    message=f"No se pudo enviar (intento {attempt} de 3): buzón lleno")
        EmailLog.objects.create(campaign=campaign, level=EmailLog.ERROR, message="No hay remitentes")
        stats = mailing.report_stats(campaign, utc(2, 16))
        self.assertEqual(stats["by_day"], [{"date": "2026-06-01", "sent": 2}, {"date": "2026-06-02", "sent": 1}])
        a, b = stats["by_sender"]
        self.assertEqual((a["email"], a["sent_today"], a["sent_total"], a["errors"]), ("a@gmail.com", 1, 3, 2))
        self.assertIn("intento 2", a["last_error"])
        self.assertEqual((b["email"], b["active"], b["sent_total"], b["errors"]), ("b@gmail.com", True, 0, 0))
        self.assertEqual([(e["message"], e["count"]) for e in stats["errors"]],
                         [("No se pudo enviar: buzón lleno", 2), ("No hay remitentes", 1)])
        self.assertEqual(self.client.get(f"{self.url}stats/").json()["per_sender_limit"], 100)
        self.assertEqual(self.client.get(f"{self.url}stats/").json()["daily_capacity"], 200)
        EmailCampaign.objects.update(enabled=False)
        self.assertEqual(self.client.get(f"{self.url}stats/").status_code, 404)

    def test_destinatarios_y_logs_paginados(self):
        campaign = make_campaign(self.dashboard, recipients=5, per_sender_limit=2)
        mailing.run_campaign(campaign, utc(1, 15))
        data = self.client.get(f"{self.url}recipients/?page=2&size=2").json()
        self.assertEqual((data["total_rows"], data["last_page"], len(data["rows"])), (5, 3, 2))
        self.assertEqual(data["rows"][0]["status"], "pending")
        sent = self.client.get(f"{self.url}recipients/?status=sent").json()
        self.assertEqual([row["sender_email"] for row in sent["rows"]], ["a@gmail.com"] * 2)
        logs = self.client.get(f"{self.url}logs/").json()
        self.assertEqual(logs["total_rows"], 2)  # inicio y fin
        self.assertIn("Fin del envío", logs["rows"][0]["message"])  # el más reciente primero
