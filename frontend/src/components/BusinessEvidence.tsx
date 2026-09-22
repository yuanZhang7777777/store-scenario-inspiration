import type { BusinessContext } from "../api";

/**
 * The numbers read off the screenshots, for checking against the store.
 *
 * Kept behind one line: it is evidence for the analysis rather than a result of
 * it, and the page is read for the analysis. No headline and no disclaimer —
 * the section says what it holds and lets the rows speak.
 */
export default function BusinessEvidence({ context }: { context?: BusinessContext | null }) {
  if (!context || !context.sales_rows.length) return null;
  const fmt = (value: number | null | undefined) => value == null ? "未读取" : value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return <details className="card" id="business-evidence">
    <summary>销售记录 <span className="count">{context.sales_rows.length} 条</span></summary>
    {context.warnings.length > 0 && <details><summary>有 {context.warnings.length} 项数据需要留意</summary>{context.warnings.map((warning, index) => <p key={index} className="muted">{warning}</p>)}</details>}
    <div className="business-table-wrap"><table className="business-table"><thead><tr><th>商品</th><th>订单数</th><th>销量（件）</th><th>成交额</th><th>周期与来源</th></tr></thead><tbody>{context.sales_rows.map((row, index) => <tr key={`${row.source_image}-${row.source_row}-${index}`}><td><strong>{row.product_name}</strong>{row.approximate && <small>页面近似值</small>}<details><summary>原文</summary>{row.raw_text}</details></td><td>{fmt(row.orders)}</td><td>{fmt(row.units_sold)}</td><td>{fmt(row.gmv)} {row.currency || ""}</td><td>{row.period_text || "周期未识别"}<small>{row.source_image}</small></td></tr>)}</tbody></table></div>
  </details>;
}
