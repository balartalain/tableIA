"""Helpers de datos compartidos por el motor y los widgets."""
import numpy as np
from django.test import SimpleTestCase

from sheets_reports.utils.data import is_number


class IsNumberTests(SimpleTestCase):
    def test_numeros_de_python_y_de_numpy(self):
        """Una suma de una columna de enteros es np.int64, que no es `int`."""
        for value in (5, 1.5, np.int64(5), np.float64(1.5), np.int32(2)):
            with self.subTest(value=repr(value)):
                self.assertTrue(is_number(value))

    def test_booleanos_y_textos_no(self):
        for value in (True, np.bool_(True), "5", None):
            with self.subTest(value=repr(value)):
                self.assertFalse(is_number(value))
