import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

import StorePage from "./pages/StorePage";
import StoresPage from "./pages/StoresPage";

function StoreRoute() {
  const { storeId } = useParams();
  return <StorePage key={storeId} />;
}

export default function App() {
  const { pathname } = useLocation();
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); }, [pathname]);
  return (
    <div className="shell">
      <header className="hero"><span className="brand-mark" aria-hidden="true">M</span><strong>METIS</strong><span>店铺分析与选品</span></header>
      <StoresPage />
      <Routes>
        <Route path="/" element={null} />
        <Route path="/stores/new" element={null} />
        <Route path="/stores/:storeId" element={<StoreRoute />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
