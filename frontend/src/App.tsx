import { Navigate, Route, Routes, useParams } from "react-router-dom";

import StorePage from "./pages/StorePage";
import StoresPage from "./pages/StoresPage";

function StoreRoute() {
  const { storeId } = useParams();
  return <StorePage key={storeId} />;
}

export default function App() {
  return (
    <div className="shell">
      <div className="hero">
        <p className="eyebrow">Store Scenario Inspiration</p>
        <h1>店铺经营助手</h1>
        <p className="subtitle">
          上传截图，查看经营建议，找到公司产品库里的可选商品。
        </p>
      </div>
      <Routes>
        <Route path="/" element={<StoresPage />} />
        <Route path="/stores/:storeId" element={<StoreRoute />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
