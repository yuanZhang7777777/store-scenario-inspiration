import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, type CustomProduct, type ParamField, type Params, type StoreSummary } from "../api";
import { countryName } from "../countries";
import { defaultStoreName, validateParams } from "../operatorUx";
import ParamsPanel from "../components/ParamsPanel";
import PhotoViewer from "../components/PhotoViewer";

interface Picked { file: File; url: string }
const MAX_FILES = 20;

export default function StoresPage() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const home = pathname === "/";
  const creating = pathname === "/stores/new";
  const currentPath = useRef(pathname);
  currentPath.current = pathname;
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [storesLoading, setStoresLoading] = useState(true);
  const [storesError, setStoresError] = useState("");
  const [countries, setCountries] = useState<string[]>([]);
  const [serviceReady, setServiceReady] = useState<boolean | null>(null);
  const [maxBytes, setMaxBytes] = useState(10 * 1024 * 1024);
  const [name, setName] = useState("");
  // Country affects stock matching; do not silently assume the Philippines.
  const [country, setCountry] = useState("");
  const [picked, setPicked] = useState<Picked[]>([]);
  // The store does not have to be described by pictures. Typing what the shop
  // sells is the same kind of answer, and for a shop nobody photographed it is
  // the only one.
  const [products, setProducts] = useState<CustomProduct[]>([]);
  const [newProduct, setNewProduct] = useState<CustomProduct>({ name_cn: "", name_en: "" });
  const [fields, setFields] = useState<ParamField[]>([]);
  const [defaults, setDefaults] = useState<Params | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [shot, setShot] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const selection = useRef<Picked[]>([]);
  const submitting = useRef(false);
  /** The settings as the form currently shows them. Held in a ref rather than
   *  state so that turning a knob does not redraw the page around it. */
  const draft = useRef<Params | null>(null);
  const onDraft = useCallback((value: Params) => { draft.current = value; }, []);

  useEffect(() => {
    let active = true;
    api.health().then((health) => {
      if (!active) return;
      setCountries(health.countries);
      setServiceReady(health.api_key_configured);
      if (health.max_upload_bytes) setMaxBytes(health.max_upload_bytes);
    }).catch(() => {
      if (active) { setServiceReady(false); setError("分析服务暂时无法连接，请稍后重试。"); }
    });
    // The defaults the run will use are the server's, not a copy kept here:
    // one field added to the model has to appear in this form by itself.
    api.paramSchema().then((schema) => {
      if (!active) return;
      setFields(schema.fields);
      setDefaults(Object.fromEntries(
        schema.fields.map((field) => [field.name, field.default])) as unknown as Params);
    }).catch(() => { if (active) setError("设置规则暂时无法加载，请刷新重试。"); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!home) return;
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const items = await api.stores();
        if (active) { setStores(items.reverse()); setStoresError(""); }
      } catch { if (active) setStoresError("店铺列表暂时无法加载，正在重试…"); }
      finally { pending = false; if (active) setStoresLoading(false); }
    };
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [home]);
  useEffect(() => () => { selection.current.forEach((item) => URL.revokeObjectURL(item.url)); }, []);

  function add(files: FileList | File[]) {
    if (busy || savedId) return;
    const accepted = [...selection.current];
    const rejected: string[] = [];
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("image/") || !file.size) { rejected.push(`${file.name}：请选择有效图片`); continue; }
      if (file.size > maxBytes) { rejected.push(`${file.name}：图片超过大小限制`); continue; }
      if (accepted.some((item) => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified)) continue;
      if (accepted.length >= MAX_FILES) { rejected.push(`一次最多上传 ${MAX_FILES} 张图片`); break; }
      accepted.push({ file, url: URL.createObjectURL(file) });
    }
    selection.current = accepted;
    setPicked(accepted);
    setError(rejected.slice(0, 3).join("；"));
  }
  function remove(index: number) {
    if (busy || savedId) return;
    URL.revokeObjectURL(selection.current[index].url);
    selection.current = selection.current.filter((_, position) => position !== index);
    setPicked([...selection.current]);
    setShot(null);
  }

  /** One press, the whole pipeline: the store is opened, the typed products are
   *  written to it, and every step runs from the reading to the candidates. The
   *  operator edits the result on the store page and runs it again from there,
   *  rather than approving the product list first and watching the rest follow. */
  async function submit() {
    if (submitting.current || busy || !serviceReady || !country) return;
    setError("");
    if (!picked.length && !products.length) {
      setError("上传一张截图，或至少填一个商品名——得先知道这家店卖什么。");
      return;
    }
    const chosen = draft.current ?? defaults;
    if (!chosen) { setError("设置规则尚未加载，请稍后重试。"); return; }
    const invalid = validateParams(chosen, fields);
    if (invalid) { setError(invalid); return; }
    submitting.current = true;
    setBusy(true);
    let id = savedId;
    try {
      if (!id) {
        const title = name.trim() || defaultStoreName(countryName(country));
        const store = await api.createStore(title, country, picked.map((item) => item.file));
        id = store.id;
        setSavedId(id);
      }
      if (products.length) await api.setProducts(id, products);
      await api.startJob(id, undefined, chosen);
      selection.current.forEach((item) => URL.revokeObjectURL(item.url));
      selection.current = [];
      setPicked([]); setProducts([]); setName(""); setCountry(""); setSavedId(null); setShot(null);
      setNewProduct({ name_cn: "", name_en: "" });
      if (currentPath.current === "/stores/new") navigate(`/stores/${id}`, { replace: true });
    } catch (failure) {
      setError(id ? "店铺已保存，但分析未启动。点“继续生成”重试，无需重新上传。" :
        failure instanceof Error ? failure.message : "上传未完成，请稍后重试。");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  const locked = busy || Boolean(savedId);

  return <>
    {home && <>
      <section className="home-intro"><div><p className="eyebrow">从店铺到选品清单</p><h1>下一份清单，从这里开始。</h1><p className="muted">上传店铺截图，自动生成分析、场景与商品，最后导出 Excel。</p></div><Link className="btn" to="/stores/new">{picked.length || products.length || savedId ? "继续填写" : "新建店铺"}<span aria-hidden="true"> ↗</span></Link></section>
      <section className="card history-card"><div className="section-heading"><h2>历史店铺</h2><span className="muted">{stores.length} 家店铺</span></div>{storesLoading ? <p className="muted" role="status">正在加载…</p> : storesError ? <p className="notice error" role="alert">{storesError}</p> : stores.length === 0 ? <p className="muted">还没有店铺。新建第一家店铺，生成结果会保存在这里。</p> : <div className="store-grid">{stores.map((store) => <Link className="store-item" key={store.id} to={`/stores/${store.id}`}><strong>{store.store_name}</strong><small>{countryName(store.country)} · {store.images.length} 张截图</small><span className="history-status" data-running={store.job_status === "running" || store.job_status === "pending"}>{store.job_status === "running" || store.job_status === "pending" ? "正在生成" : store.job_status === "failed" || store.job_status === "cancelled" ? "待继续生成" : store.needs_update ? "待更新" : store.stages.retrieval ? "查看结果与导出" : "继续生成"}<span aria-hidden="true"> →</span></span></Link>)}</div>}</section>
    </>}
    <div hidden={!creating}>
    <p className="crumb"><Link to="/">← 所有店铺</Link></p>
    <section className="card upload-card operator-upload" onPaste={(event) => {
      if (event.clipboardData.files.length && !locked) {
        event.preventDefault(); add(event.clipboardData.files);
      }
    }}>
      <div className="section-heading"><div><h1>新建店铺</h1><p className="muted">选择销售国家，上传截图或填写商品名。开始后会自动完成全部步骤。</p></div></div>
      <ol className="operator-steps" aria-label="使用流程"><li aria-current="step"><span>1</span>提供店铺资料</li><li><span>2</span>开始生成</li><li><span>3</span>导出 Excel</li></ol>
      <fieldset disabled={locked} className="plain-fieldset">
        <div className="field-row">
          <div className="field"><label htmlFor="country">销售国家</label><select id="country" required value={country} onChange={(event) => setCountry(event.target.value)}><option value="" disabled>选择店铺所在的市场</option>{countries.map((code) => <option value={code} key={code}>{countryName(code)}</option>)}</select></div>
          <div className="field"><label htmlFor="store-name">店铺名称 <span className="muted">选填</span></label><input id="store-name" value={name} maxLength={120} placeholder="不填则自动命名" onChange={(event) => setName(event.target.value)} /></div>
        </div>
        <div className={over ? "drop over" : "drop"}
          onDragOver={(event) => { event.preventDefault(); if (!locked) setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => { event.preventDefault(); setOver(false); add(event.dataTransfer.files); }}>
          <button className="upload-trigger" type="button" onClick={() => input.current?.click()}>选择截图</button>
          <span>也可以拖入图片，或在此页面粘贴截图。</span>
          <input aria-label="上传店铺截图" ref={input} type="file" accept="image/*" multiple hidden onChange={(event) => { if (event.target.files) add(event.target.files); event.target.value = ""; }} />
        </div>
        <p className="muted operator-upload-hint">商品页、热卖榜均可。销售明细截图请保留表头、币种和统计周期。</p>
        {picked.length > 0 && <div className="thumbs">{picked.map((item, index) => <div className="thumb" key={item.url}><button type="button" className="shot" title="放大查看" onClick={() => setShot(index)}><img src={item.url} alt={item.file.name} /></button><span>{item.file.name}</span><button type="button" aria-label={`移除 ${item.file.name}`} onClick={() => remove(index)}>×</button></div>)}</div>}
        <p className="muted">已选 {picked.length}/{MAX_FILES} 张 · 单张不超过 {(maxBytes / 1024 / 1024).toFixed(0)} MB</p>

        {/* A shop can be described without a single picture. What is typed here
            reaches the same product list a recognised product does, so a store
            made of one typed name still gets its related SKUs. */}
        <div className="add-product">
          <span className="lbl">填写商品名 <span className="muted">与截图至少提供一种，也可一起补充</span></span>
          <div className="add-product-row">
            <input value={newProduct.name_cn} maxLength={120} placeholder="商品中文名（必填）"
              onChange={(event) => setNewProduct({ ...newProduct, name_cn: event.target.value })} />
            <input value={newProduct.name_en} maxLength={120} placeholder="英文名（选填）"
              onChange={(event) => setNewProduct({ ...newProduct, name_en: event.target.value })} />
            <button className="btn ghost small" type="button" disabled={!newProduct.name_cn.trim()}
              onClick={() => {
                setProducts([...products, { name_cn: newProduct.name_cn.trim(), name_en: newProduct.name_en.trim() }]);
                setNewProduct({ name_cn: "", name_en: "" });
              }}>加入</button>
          </div>
          {products.length > 0 && <div className="clue-list">
            {products.map((item, index) => (
              <div className="clue pick-clue" key={`${item.name_cn}-${index}`}>
                <span><strong>{item.name_cn}</strong>{item.name_en && <small className="original">{item.name_en}</small>}</span>
                <span className="clue-tags">
                  <button className="btn ghost small" type="button" title="从名单里删掉这条"
                    onClick={() => setProducts(products.filter((_, position) => position !== index))}>删掉</button>
                </span>
              </div>
            ))}
          </div>}
        </div>

        {defaults && <ParamsPanel params={defaults} busy={busy} onDraft={onDraft} />}
      </fieldset>
      {serviceReady === null && <p className="muted" role="status">正在连接分析服务…</p>}
      {serviceReady === false && <p className="notice warn">分析服务暂未就绪，请联系维护人员。已选图片会保留。</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <div className="form-actions"><button className="btn" disabled={busy || !serviceReady || !country || (!picked.length && !products.length)} onClick={submit}>{busy ? "正在提交…" : savedId ? "继续生成" : "开始生成"}</button><span className="muted">生成期间可先阅读店铺分析</span>{savedId && <Link className="btn ghost" to={`/stores/${savedId}`}>查看已保存资料</Link>}</div>
    </section>
    {creating && <PhotoViewer photos={picked.map((item) => ({ src: item.url, label: item.file.name }))} index={shot} onIndex={setShot} onClose={() => setShot(null)} />}
    </div>
  </>;
}
