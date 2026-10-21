"""Static human-readable report rendered only from the frozen export DTO."""
import html
import json


def render(out):
    dto=out.get('body') or {}
    header=dto.get('header') or {}
    esc=lambda value:html.escape(str(value),quote=True)
    identity=' · '.join(str(header.get(k) or '') for k in ('strategy','market','symbol'))
    fields=[('原确认',header.get('original_confirmation')),('完整资格',header.get('qualification')),
        ('后续观察',header.get('observation')),('数据质量',header.get('data_quality'))]
    sections=''.join('<h2>'+esc(name)+'</h2><pre>'+esc(json.dumps(value,ensure_ascii=False,indent=2))+'</pre>' for name,value in fields)
    rows=dto.get('rows') or []
    keys=list(rows[0]) if rows else []
    table='<table><thead><tr>'+''.join('<th>'+esc(k)+'</th>' for k in keys)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+esc(r.get(k,''))+'</td>' for k in keys)+'</tr>' for r in rows)+'</tbody></table>'
    metadata=json.dumps(dict(read_view=dto.get('read_view') or out.get('read_view'),
        next_cursor=dto.get('next_cursor'),SOURCE_VERSION=dto.get('SOURCE_VERSION'),
        limit=dto.get('limit'),historical_import=header.get('historical_import')),ensure_ascii=False,indent=2)
    # No scripts, remote resources, live queries or AI; user may print this HTML.
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<title>ABC 持续追踪报告</title><style>body{font:14px system-ui;margin:32px;color:#172033}h1{font-size:24px}'
        'table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #ccd2dc;padding:8px;vertical-align:top}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px}h2{font-size:17px}</style>'
        '<h1>ABC 持续追踪报告</h1><p>'+esc(identity)+'</p><p>原确认、资格和后续观察分别保留；历史记录不作为实时机会。</p>'
        +sections+'<h2>本页修订</h2>'+table+'<h2>固定视图与分页</h2><pre>'+esc(metadata)+'</pre>'
        '<p>有 next_cursor 时本文件仅为当前页；继续导出下一页。报告未调用 AI。</p></html>').encode('utf-8')

