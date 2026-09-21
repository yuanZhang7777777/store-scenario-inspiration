import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, type StoreSummary } from "../api";

interface Picked {
  file: File;
  url: string;
}

export default function StoresPage() {
  const navigate = useNavigate();
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [countries, setCountries] = useState<string[]>(["PH", "TH", "VN", "MY"]);
  const [apiKeyReady, setApiKeyReady] = useState(true);
  const [name, setName] = useState("");
  const [country, setCountry] = useState("PH");
  const [picked, setPicked] = useState<Picked[]>([]);
  const [over, setOver] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.stores().then(setStores).catch((e) => setError(e.message));
    api
      .health()
      .then((h) => {
        setCountries(h.countries);
        setApiKeyReady(h.api_key_configured);
      })
      .catch((e) => setError(e.message));
  }, []);

  const add = useCallback((files: FileList | File[]) => {
    const images = Array.from(files).filter((file) => file.type.startsWith("image/"));
    setPicked((current) => [
      ...current,
      ...images.map((file) => ({ file, url: URL.createObjectURL(file) })),
    ]);
  }, []);

  const drop = useCallback(
    (index: number) => {
      setPicked((current) => {
        URL.revokeObjectURL(current[index].url);
        return current.filter((_, position) => position !== index);
      });
    },
    [],
  );

  const total = useMemo(
    () => picked.reduce((sum, item) => sum + item.file.size, 0),
    [picked],
  );

  async function submit() {
    setError("");
    setBusy(true);
    try {
      const store = await api.createStore(name, country, picked.map((item) => item.file));
      await api.startJob(store.id);
      navigate(`/stores/${store.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <section className="card" style={{ padding: 22, marginBottom: 16 }}>
        <h2>新建店铺</h2>

        <div className="field-row">
          <div className="field">
            <label htmlFor="store-name">店铺名称</label>
            <input
              id="store-name"
              value={name}
              placeholder="例如 Shopee-13021PH"
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="country">目标国家</label>
            <select id="country" value={country} onChange={(event) => setCountry(event.target.value)}>
              {countries.map((code) => (
                <option key={code}>{code}</option>
              ))}
            </select>
          </div>
        </div>

        <div
          className={over ? "drop over" : "drop"}
          onDragOver={(event) => {
            event.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setOver(false);
            add(event.dataTransfer.files);
          }}
          onClick={() => input.current?.click()}
          style={{ cursor: "pointer" }}
        >
          <strong>把店铺截图拖进来</strong>
          <span>或者点这里选择文件。一般 2–5 张就够，越多越准。</span>
          <input
            ref={input}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(event) => {
              if (event.target.files) add(event.target.files);
              event.target.value = "";
            }}
          />
        </div>

        {picked.length > 0 && (
          <>
            <div className="thumbs">
              {picked.map((item, index) => (
                <div className="thumb" key={item.url}>
                  <img src={item.url} alt={item.file.name} />
                  <span>{item.file.name}</span>
                  <button onClick={() => drop(index)} title="移除">
                    ×
                  </button>
                </div>
              ))}
            </div>
            <p className="muted" style={{ marginTop: 10 }}>
              已选 {picked.length} 张，共 {(total / 1024 / 1024).toFixed(1)} MB
            </p>
          </>
        )}

        {!apiKeyReady && (
          <p className="notice warn" style={{ marginTop: 14 }}>
            没有检测到 DEEPSEEK_API_KEY。识别和场景生成要调用 DeepSeek，请先配置再运行。
          </p>
        )}
        {error && (
          <p className="notice error" style={{ marginTop: 14 }}>
            {error}
          </p>
        )}

        <div style={{ marginTop: 18 }}>
          <button className="btn" disabled={busy || !name.trim() || picked.length === 0} onClick={submit}>
            {busy ? "正在上传…" : "上传并生成场景"}
          </button>
        </div>
      </section>

      <section className="card">
        <h2>已有店铺</h2>
        {stores.length === 0 ? (
          <p className="muted">还没有店铺。</p>
        ) : (
          <div className="store-grid">
            {stores.map((store) => (
              <Link className="store-item" key={store.id} to={`/stores/${store.id}`}>
                <strong>{store.store_name}</strong>
                <small>
                  {store.country} · {store.images.length} 张截图 ·{" "}
                  {store.stages.scenes ? "已生成场景" : "未生成场景"}
                </small>
              </Link>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
