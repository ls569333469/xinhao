const assert=require('node:assert/strict');
const format=require('./design/report-format.js');
const examples=[
 ['| 名称 | 值 |\n| --- | ---: |\n| **A** | 2 |','<table>'],
 ['```js\n<script>alert(1)</script>\n```','&lt;script&gt;alert(1)&lt;/script&gt;'],
 ['```\n**literal** | a\n```','**literal** | a'],
 ['~~~\nunclosed','<pre><code>unclosed</code></pre>'],
 ['1. first\n2. second\n\nnext','</ol>\n<p>next</p>'],
 ['<img src=x onerror=alert(1)>','&lt;img'],
 ['**bold** and `**code**`','<code>**code**</code>'],
 ['| A | B |\n| --- | --- |\n| one \\| two | three |','one | two'],
];
for(const [text,expected] of examples){const html=format.markdown(text);assert.ok(html.includes(expected),html);assert.ok(!/<(?:script|img|iframe)\b/i.test(html));}
assert.ok(format.looksMarkdown('```\nx\n```'));
assert.ok(format.looksMarkdown('|a|b|\n|---|---|'));
assert.equal(format.looksMarkdown('plain text'),false);
console.log('report format: 11 checks passed, including HTML escaping, fenced code, tables and ordered lists');
