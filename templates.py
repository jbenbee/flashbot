import os


class Templates:
    def __init__(self, path):
        self._templates = dict()

        for lang in os.listdir(path):
            lang_path = os.path.join(path, lang)

            if os.path.isdir(lang_path):
                self._templates[lang] = {}

                for item in os.listdir(lang_path):

                    template_path = os.path.join(lang_path, item)

                    if os.path.isfile(template_path):
                        with open(template_path, 'r', encoding='utf-8') as file:
                            tname = os.path.splitext(item)[0]
                            self._templates[lang][tname] = file.read()
                    else:
                        dst_lang = item
                        self._templates[lang][dst_lang] = {}
                        for tname in os.listdir(template_path):
                            tpath = os.path.join(lang_path, dst_lang, tname)
                            if os.path.isfile(tpath):
                                with open(tpath, 'r', encoding='utf-8') as file:
                                    tfilename = os.path.splitext(tname)[0]
                                    self._templates[lang][dst_lang][tfilename] = file.read()


    def get_template(self, uilang: str, lang: str, template_name: str) -> str:
        template = self._templates[uilang][template_name] if template_name in self._templates[uilang].keys() else \
                                self._templates[uilang][lang][template_name]
        return template