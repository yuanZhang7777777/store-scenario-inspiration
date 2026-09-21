import { Navigate, Route, Routes } from "react-router-dom";

import StorePage from "./pages/StorePage";
import StoresPage from "./pages/StoresPage";

export default function App() {
  return (
    <div className="shell">
      <div className="hero">
        <p className="eyebrow">Store Scenario Inspiration</p>
        <h1>店铺场景灵感助手</h1>
        <p className="subtitle">
          上传店铺截图，系统识别商品、推演使用场景，再去产品库里为场景里的每个商品角色召回候选 SKU。
          识别出来的商品默认全部保留，不属于这家店的取消勾选即可；候选按相关度排好，勾选你要采用的。
        </p>
      </div>
      <Routes>
        <Route path="/" element={<StoresPage />} />
        <Route path="/stores/:storeId" element={<StorePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
