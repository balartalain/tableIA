code = '''
class Test:
    def __init__(self, form=None):
        form = form or {}
        self.form = form
        self.id = form.get('id')
'''
compile(code, 'test', 'exec')
print('Isolated class OK')
