import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type StoreSummary } from "../api";
import { countryName } from "../countries";
import { defaultStoreName } from "../operatorUx";
import PhotoViewer from "../components/PhotoViewer";

interface Picked { file: File; url: string }
const MAX_FILES = 20;

export default function StoresPage() {
  const navigate = useNavigate();
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [shot, setShot] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const selection = useRef<Picked[]>([]);
  const submitting = useRef(false);

  useEffect(() => {
    let active = true;
    api.stores().then((items) => { if (active) setStores(items); })
      .catch(() => { if (active) setStoresError("已有分析暂时无法加载，请刷新重试。"); })
      .finally(() => { if (active) setStoresLoading(false); });
    api.health().then((health) => {
      if (!active) return;
      setCountries(health.countries);
      setServiceReady(health.api_key_configured);
      if (health.max_upload_bytes) setMaxBytes(health.max_upload_bytes);
    }).catch(() => {
      if (active) { setServiceReady(false); setError("分析服务暂时无法连接，请稍后重试。"); }
    });
    return () => { active = false; };
  }, []);
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
  async function submit() {
    if (submitting.current || !serviceReady || !country || !picked.length) return;
    setError("");
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
      await api.startJob(id, ["recognize", "clues"]);
      navigate(`/stores/${id}`);
    } catch (failure) {
      setError(id ? "截图已保存，但识别未启动。点击“继续识别”重试，无需重新上传。" :
        failure instanceof Error ? failure.message : "上传未完成，请稍后重试。");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return <>
    <section className="card upload-card operator-upload" onPaste={(event) => {
      if (event.clipboardData.files.length && !busy && !savedId) {
        event.preventDefault(); add(event.clipboardData.files);
      }
    }}>
      <div className="section-heading"><div><h2>先上传几张店铺截图</h2><p className="muted">看经营方向、找推荐商品，不用先研究参数。</p></div></div>
      <ol className="operator-steps" aria-label="使用流程"><li aria-current="step"><span>1</span>上传截图</li><li><span>2</span>核对商品</li><li><span>3</span>查看经营建议</li></ol>
      <fieldset disabled={busy || Boolean(savedId)} className="plain-fieldset">
        <div className="field-row">
          <div className="field"><label htmlFor="country">销售国家</label><select id="country" required value={country} onChange={(event) => setCountry(event.target.value)}><option value="" disabled>选择店铺所在的市场</option>{countries.map((code) => <option value={code} key={code}>{countryName(code)}</option>)}</select></div>
          <div className="field"><label htmlFor="store-name">店铺名称 <span className="muted">选填</span></label><input id="store-name" value={name} maxLength={120} placeholder="不填则自动命名" onChange={(event) => setName(event.target.value)} /></div>
        </div>
        <div className={over ? "drop over" : "drop"}
          onDragOver={(event) => { event.preventDefault(); if (!busy && !savedId) setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => { event.preventDefault(); setOver(false); add(event.dataTransfer.files); }}>
          <button className="upload-trigger" type="button" onClick={() => input.current?.click()}>选择截图</button>
          <span>也可以拖入图片，或在此页面粘贴截图。</span>
          <input aria-label="上传店铺截图" ref={input} type="file" accept="image/*" multiple hidden onChange={(event) => { if (event.target.files) add(event.target.files); event.target.value = ""; }} />
        </div>
        <p className="muted operator-upload-hint">商品页、热卖榜均可。销售明细截图请保留表头、币种和统计周期。</p>
        {picked.length > 0 && <div className="thumbs">{picked.map((item, index) => <div className="thumb" key={item.url}><button type="button" className="shot" title="放大查看" onClick={() => setShot(index)}><img src={item.url} alt={item.file.name} /></button><span>{item.file.name}</span><button type="button" aria-label={`移除 ${item.file.name}`} onClick={() => remove(index)}>×</button></div>)}</div>}
        <p className="muted">已选 {picked.length}/{MAX_FILES} 张 · 单张不超过 {(maxBytes / 1024 / 1024).toFixed(0)} MB</p>
      </fieldset>
      {serviceReady === null && <p className="muted" role="status">正在连接分析服务…</p>}
      {serviceReady === false && <p className="notice warn">分析服务暂未就绪，请联系维护人员。已选图片会保留。</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <div className="form-actions"><button className="btn" disabled={busy || !serviceReady || !country || !picked.length} onClick={submit}>{busy ? "正在处理…" : savedId ? "继续识别" : "下一步：识别商品"}</button>{savedId && <Link className="btn ghost" to={`/stores/${savedId}`}>查看已保存资料</Link>}</div>
    </section>
    <section className="card"><h2>已有分析</h2>{storesLoading ? <p className="muted" role="status">正在加载…</p> : storesError ? <p className="notice error" role="alert">{storesError}</p> : stores.length === 0 ? <p className="muted">分析会保存在这里，之后可以继续查看。</p> : <div className="store-grid">{stores.map((store) => <Link className="store-item" key={store.id} to={`/stores/${store.id}`}><strong>{store.store_name}</strong><small>{countryName(store.country)} · {store.images.length} 张截图 · {store.stages.retrieval ? "查看商品推荐" : store.stages.synthesis ? "查看经营建议" : store.stages.recognized ? "继续核对商品" : "继续识别"}</small></Link>)}</div>}</section>
    <PhotoViewer photos={picked.map((item) => ({ src: item.url, label: item.file.name }))} index={shot} onIndex={setShot} onClose={() => setShot(null)} />
  </>;
}
