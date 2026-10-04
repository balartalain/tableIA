code = '''
class Test:
    def __init__(self, form=None):
        form = form or {}
        self.form = form
        self.id = form.get('id')
        self._chart = None
'''
compile(code, 'test', 'exec')
print('Isolated class OK')
