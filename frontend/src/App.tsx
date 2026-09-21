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
        <h1>店铺场景灵感助手</h1>
        <p className="subtitle">
          分析店铺、商品与经营方向，匹配公司产品库中的 SKU，形成可供上架选择的商品清单。
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
