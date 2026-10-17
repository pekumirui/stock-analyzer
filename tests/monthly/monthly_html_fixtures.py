"""月次 HTML のテストが共有する見本と、銘柄パーサーの単位宣言の読み取り。test_ で始まらないので pytest は集めない。"""
from __future__ import annotations


CAWACHI_HTML = """
<html><body>
<h2>売上高前年同月比</h2>
<h3>【連結】2027年3月期（2026年3月16日～2027年3月15日）</h3>
<table>
<thead><tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月 </th>
<th>9月 </th><th>10月 </th><th>11月  </th><th>12月 </th><th>1月</th><th>2月</th><th>3月</th>
<th>累計 </th></tr></thead>
<tbody>
<tr><td>全店<br></td><td>1.9</td><td>2.0</td><td>0.3</td><td>△4.3</td><td>△1.9</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td>△0.5</td></tr>
<tr><td>既存店</td><td>1.3</td><td>1.8</td><td>0.4</td><td>△3.8</td><td>△1.5</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td>△0.4</td></tr>
</tbody>
<tfoot><tr><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td>(%)</td></tr></tfoot>
</table>
</body></html>
"""


COLOWIDE_HTML = """
<p>（2026年9月4日更新）</p>
<div class="tableWrapperTypeScroll irtable_sale">
<table class="tableType02"><tbody>
<tr><th rowspan="2">2026年度</th><th colspan="3">既存店舗（全業態）</th><th colspan="3">全店舗（全業態）</th></tr>
<tr><th>売上高</th><th>客数</th><th>客単価</th><th>売上高</th><th>客数</th><th>客単価</th></tr>
<tr><th>4月</th><td>101.8％</td><td>100.5％</td><td>101.3％</td><td>120.0％</td><td>150.9％</td><td>79.6％</td></tr>
<tr><th>8月</th><td>99.5％</td><td>99.5％</td><td>99.9％</td><td>115.2％</td><td>142.4％</td><td>80.9％</td></tr>
<tr><th>9月</th><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td></tr>
<tr><th>3月</th><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td></tr>
<tr><th>通期計</th><td>102.0％</td><td>100.9％</td><td>101.1％</td><td>120.1％</td><td>150.0％</td><td>80.0％</td></tr>
</tbody></table></div>
"""


MOS_HTML = """
<p>2026年4月～2027年3月（対前年比：%）</p>
<table>
<tr><th></th><th>既存店売上高</th><th>既存店客数</th><th>全店売上高</th></tr>
<tr><th>4月</th><td>102.6</td><td>102.7</td><td>102.0</td></tr>
<tr><th>8月</th><td>103.9</td><td>100.3</td><td>103.5</td></tr>
<tr><th>9月</th><td></td><td></td><td></td></tr>
<tr><th>上期計</th><td>103.8</td><td>102.2</td><td>103.2</td></tr>
<tr><th>3月</th><td></td><td></td><td></td></tr>
<tr><th>下期計</th><td>103.8</td><td>102.2</td><td>103.2</td></tr>
<tr><th>通期計</th><td>103.8</td><td>102.2</td><td>103.2</td></tr>
</table>
"""


ROYAL_DUAL_BRAND_HTML = """
<html><body>
<nav>
<ul>
<li><a href="#royalhost">ロイヤルホスト</a></li>
<li><a href="#tenya">てんや</a></li>
</ul>
</nav>
<section id="royalhost">
<h2>ロイヤルホスト</h2>
<h3>2026年（令和8年）（既存店前年比）</h3>
<table>
<tr><th></th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>上半期</th></tr>
<tr><th>売上高</th><td>106.5</td><td>104.3</td><td>104.4</td><td>106.1</td><td>112.2</td><td>99.8</td><td>105.6</td></tr>
<tr><th>来客数</th><td>100.1</td><td>98.9</td><td>98.7</td><td>99.7</td><td>105.9</td><td>92.7</td><td>99.3</td></tr>
<tr><th>客単価</th><td>106.4</td><td>105.5</td><td>105.7</td><td>106.4</td><td>105.9</td><td>107.7</td><td>106.3</td></tr>
</table>
<table>
<tr><th></th><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>下半期</th><th>年間</th></tr>
<tr><th>売上高</th><td>106.8</td><td>108.1</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>来客数</th><td>100.0</td><td>101.3</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>客単価</th><td>106.7</td><td>106.8</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
<p>（単位：％）</p>
<ul>
<li>上記数値は2026年8月度の確定値です。</li>
</ul>
</section>
<section id="tenya">
<h2>てんや</h2>
<h3>2026年（令和8年）（既存店前年比）</h3>
<table>
<tr><th></th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>上半期</th></tr>
<tr><th>売上高</th><td>106.2</td><td>105.6</td><td>106.6</td><td>100.1</td><td>103.2</td><td>98.4</td><td>103.4</td></tr>
<tr><th>来客数</th><td>94.1</td><td>97.6</td><td>100.1</td><td>94.3</td><td>97.3</td><td>92.1</td><td>95.9</td></tr>
<tr><th>客単価</th><td>112.9</td><td>108.2</td><td>106.5</td><td>106.2</td><td>106.0</td><td>106.9</td><td>107.7</td></tr>
</table>
<table>
<tr><th></th><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>下半期</th><th>年間</th></tr>
<tr><th>売上高</th><td>102.3</td><td>98.0</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>来客数</th><td>94.7</td><td>90.6</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>客単価</th><td>108.0</td><td>108.2</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
<p>（単位：％）</p>
<ul>
<li>上記数値は2026年8月度の確定値です。</li>
</ul>
</section>
</body></html>
"""


SEKISUI_MONTH_HTML = """
<html><body>
<h1>2026年8月度受注速報（対前年同期比）（2026年2月～2027年1月）</h1>
<table>
<tr><th></th><th>累計 （2026年2月～2026年8月）</th><th>単月 （2026年8月）</th></tr>
<tr><td>全体</td><td>101%</td><td>94%</td></tr>
<tr><td>【戸建住宅】</td><td>101%</td><td>102%</td></tr>
<tr><td>【賃貸･事業用建物 (RC造除く)】</td><td>93%</td><td>78%</td></tr>
<tr><td>【リフォーム】</td><td>117%</td><td>111%</td></tr>
<tr><td>【マンション】</td><td>126%</td><td>131%</td></tr>
</table>
</body></html>
"""


def module_force_units() -> dict[str, str]:
    """i<code>.py の単位宣言。Page.unit、または force_unit / _apply_force_unit。"""
    import importlib
    import inspect
    import pkgutil
    import re

    from jobs.monthly import html_issuers

    decl = re.compile(
        r"""(?:force_unit\s*=\s*|_apply_force_unit\(\s*\w+\s*,\s*)"""
        r"""["'](number|yoy_index|yoy_pct)["']"""
    )
    out: dict[str, str] = {}
    for info in pkgutil.iter_modules(html_issuers.__path__):
        if not info.name.startswith("i"):
            continue
        mod = importlib.import_module(f"jobs.monthly.html_issuers.{info.name}")
        page = getattr(mod, "PAGE", None)
        if page is not None:
            if page.unit:
                out[str(page.code)] = str(page.unit)
            continue
        found = decl.findall(inspect.getsource(mod))
        if not found:
            continue
        if len(set(found)) != 1:
            raise SystemExit(f"{info.name} mixed units {found}")
        out[info.name[1:]] = found[0]
    return out
