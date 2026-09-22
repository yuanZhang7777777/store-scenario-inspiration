import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type StoreSummary } from "../api";
import { countryName } from "../countries";
import PhotoViewer from "../components/PhotoViewer";

interface Picked { file: File; url: string }
const MAX_FILES = 20;

export default function StoresPage() {
  const navigate = useNavigate();
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [countries, setCountries] = useState(["PH", "TH", "VN", "MY"]);
  const [serviceReady, setServiceReady] = useState<boolean | null>(null);
  const [maxBytes, setMaxBytes] = useState(10 * 1024 * 1024);
  const [name, setName] = useState("");
  const [country, setCountry] = useState("PH");
  const [picked, setPicked] = useState<Picked[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  /** Which of the just-picked files is open full size, or none. */
  const [shot, setShot] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const selection = useRef<Picked[]>([]);
  const submitting = useRef(false);

  useEffect(() => {
    let active = true;
    api.stores().then((items) => { if (active) setStores(items); }).catch(() => {
      if (active) setError("暂时无法读取已有店铺，请刷新后重试。");
    });
    api.health().then((health) => {
      if (!active) return;
      setCountries(health.countries);
      setServiceReady(health.api_key_configured);
      if (health.max_upload_bytes) setMaxBytes(health.max_upload_bytes);
    }).catch(() => {
      if (active) { setServiceReady(false); setError("分析服务暂时无法连接，请检查后端是否启动。"); }
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
  }
  async function submit() {
    if (submitting.current || !serviceReady || !name.trim() || !picked.length) return;
    setError("");
    submitting.current = true;
    setBusy(true);
    let id = savedId;
    try {
      if (!id) {
        const store = await api.createStore(name.trim(), country, picked.map((item) => item.file));
        id = store.id;
        setSavedId(id);
      }
      await api.startJob(id, ["recognize", "clues"]);
      navigate(`/stores/${id}`);
    } catch (failure) {
      setError(id ? "资料已保存，但识别未能启动。可点击“继续识别”重试，不会重复上传。" :
        failure instanceof Error ? failure.message : "上传未完成，请稍后重试。");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <section className="card upload-card">
        <div className="section-heading"><div><h2>分析一家店铺</h2><p className="muted">上传商品与销售截图。识别完成后先确认信息，再开始分析和选品。</p></div><span className="quiet-badge">店铺分析 → 场景选品</span></div>
        <fieldset disabled={busy || Boolean(savedId)} className="plain-fieldset">
          <div className="field-row">
            <div className="field"><label htmlFor="store-name">店铺名称</label><input id="store-name" value={name} maxLength={120} placeholder="填写店铺名称" onChange={(event) => setName(event.target.value)} /></div>
            <div className="field"><label htmlFor="country">国家</label><select id="country" value={country} onChange={(event) => setCountry(event.target.value)}>{countries.map((code) => <option value={code} key={code}>{countryName(code)}</option>)}</select></div>
          </div>
          <div className={over ? "drop over" : "drop"}
            onDragOver={(event) => { event.preventDefault(); if (!busy && !savedId) setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(event) => { event.preventDefault(); setOver(false); add(event.dataTransfer.files); }}>
            <button className="upload-trigger" type="button" onClick={() => input.current?.click()}>选择或拖入店铺截图</button>
            <span>商品页、热卖榜或销售明细均可。销售截图请保留表头、币种和统计周期。</span>
            <input aria-label="上传店铺截图" ref={input} type="file" accept="image/*" multiple hidden onChange={(event) => { if (event.target.files) add(event.target.files); event.target.value = ""; }} />
          </div>
          {picked.length > 0 && <div className="thumbs">{picked.map((item, index) => <div className="thumb" key={item.url}><button type="button" className="shot" title="点击放大查看" onClick={() => setShot(index)}><img src={item.url} alt={item.file.name} /></button><span>{item.file.name}</span><button type="button" aria-label={`移除 ${item.file.name}`} onClick={() => remove(index)}>×</button></div>)}</div>}
          <p className="muted">已选 {picked.length} 张 · 单张不超过 {(maxBytes / 1024 / 1024).toFixed(0)} MB</p>
        </fieldset>
        {serviceReady === false && <p className="notice warn">分析服务尚未就绪，请联系维护人员检查配置。已选图片会保留。</p>}
        {error && <p className="notice error" role="alert">{error}</p>}
        <div className="form-actions"><button className="btn" disabled={busy || !serviceReady || !name.trim() || !picked.length} onClick={submit}>{busy ? "正在处理…" : savedId ? "继续识别" : "上传并识别"}</button>{savedId && <Link className="btn ghost" to={`/stores/${savedId}`}>查看已保存资料</Link>}</div>
      </section>
      <section className="card"><h2>已有店铺</h2>{stores.length === 0 ? <p className="muted">完成首次上传后，店铺将保存在这里。</p> : <div className="store-grid">{stores.map((store) => <Link className="store-item" key={store.id} to={`/stores/${store.id}`}><strong>{store.store_name}</strong><small>{countryName(store.country)} · {store.images.length} 张截图 · {store.stages.retrieval ? "已有商品推荐" : store.stages.synthesis ? "已有分析" : "待分析"}</small></Link>)}</div>}</section>

      <PhotoViewer
        photos={picked.map((item) => ({ src: item.url, label: item.file.name }))}
        index={shot}
        onIndex={setShot}
        onClose={() => setShot(null)}
      />
    </>
  );
}
