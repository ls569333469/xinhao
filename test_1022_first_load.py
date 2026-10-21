"""The page shell is present in the first HTML response, before JS starts."""
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).parent
class FirstResponse(unittest.TestCase):
 def test_tracking_runtime_present_once_before_external_integration(self):
  html=(ROOT/'design/prototype.html').read_text(encoding='utf-8')
  js=(ROOT/'design/tracking-workbench.js').read_text(encoding='utf-8')
  runtime='<script data-tracking-runtime="inline">\n'+js+'</script>'
  self.assertEqual(html.count(runtime),1)
  self.assertNotIn('src="/tracking-workbench.js"',html)
  self.assertLess(html.index(runtime),html.index('<script src="/workbench-integrity.js"'))
  self.assertNotIn('</script',js.lower())
 def test_main_template_contains_exact_current_fragment(self):
  html=(ROOT/'design/prototype.html').read_text(encoding='utf-8')
  fragment=(ROOT/'design/tracking-workbench.html').read_text(encoding='utf-8')
  self.assertEqual(html.count(fragment),1)
  self.assertIn('<section data-view="tracking" hidden>'+fragment+'</section>',html)
  self.assertIn('正在连接已保存的追踪记录',html)
  self.assertIn('id="tw-rows"',html)
  self.assertIn('id="tw-tab-history"',html)
 def test_fragment_is_scoped_to_new_hidden_page(self):
  html=(ROOT/'design/prototype.html').read_text(encoding='utf-8')
  fragment=(ROOT/'design/tracking-workbench.html').read_text(encoding='utf-8')
  without=html.replace('<section data-view="tracking" hidden>'+fragment+'</section>', '<section data-view="tracking" hidden></section>',1)
  self.assertNotIn('id="trackingWorkbench"',without)
  self.assertEqual(without.count('<section data-view="tracking" hidden></section>'),1)
 def test_main_document_has_no_duplicate_element_ids(self):
  class Ids(HTMLParser):
   def __init__(self):super().__init__();self.ids=[]
   def handle_starttag(self,tag,attrs):
    for key,value in attrs:
     if key=='id':self.ids.append(value)
  parser=Ids();parser.feed((ROOT/'design/prototype.html').read_text(encoding='utf-8'))
  self.assertEqual(len(parser.ids),len(set(parser.ids)))

if __name__=='__main__':unittest.main()
