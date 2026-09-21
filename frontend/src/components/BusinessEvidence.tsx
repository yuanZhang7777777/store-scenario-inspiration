import type { BusinessContext } from "../api";

export default function BusinessEvidence({ context }: { context?: BusinessContext | null }) {
  if (!context) return null;
  const data = context.store_metrics;
  const fmt = (value: number | null | undefined) => value == null ? "未提供" : value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return <section className="card" id="business-evidence">
    <h2>经营数据依据</h2>
    <div className="metric-strip"><div><span>ADO</span><strong>{fmt(data.ado)}</strong></div><div><span>ADG {data.currency || ""}</span><strong>{fmt(data.adg)}</strong></div><div><span>统计周期</span><strong className="metric-period">{data.period_start && data.period_end ? `${data.period_start} 至 ${data.period_end}` : "未提供"}</strong></div></div>
    <p className="muted">已识别 {context.sales_rows.length} 条商品销售记录，仅代表上传截图范围，不等于全店销售分布。</p>
    {context.warnings.length > 0 && <details><summary>有 {context.warnings.length} 项数据需要留意</summary>{context.warnings.map((warning, index) => <p key={index} className="muted">{warning}</p>)}</details>}
    {context.sales_rows.length > 0 && <details><summary>查看商品销售依据</summary><div className="business-table-wrap"><table className="business-table"><thead><tr><th>商品</th><th>订单数</th><th>销量（件）</th><th>成交额</th><th>周期与来源</th></tr></thead><tbody>{context.sales_rows.map((row, index) => <tr key={`${row.source_image}-${row.source_row}-${index}`}><td><strong>{row.product_name}</strong>{row.approximate && <small>页面近似值</small>}<details><summary>原文</summary>{row.raw_text}</details></td><td>{fmt(row.orders)}</td><td>{fmt(row.units_sold)}</td><td>{fmt(row.gmv)} {row.currency || ""}</td><td>{row.period_text || "周期未识别"}<small>{row.source_image}</small></td></tr>)}</tbody></table></div></details>}
  </section>;
}
