import { Routes, Route, Navigate } from 'react-router';
import { Suspense, lazy } from 'react';
import { ProtectedRoute } from './ProtectedRoute';

// Lazy load pages for better performance
const Home = lazy(() => import('../pages/Home'));
const Register = lazy(() => import('../pages/Register'));
const Login = lazy(() => import('../pages/Login'));
const ItemsCatalog = lazy(() => import('../pages/ItemsCatalog'));
const ItemDetail = lazy(() => import('../pages/ItemDetail'));

const LoadingSpinner = () => (
  <div role="status" aria-label="Loading page" className="flex items-center justify-center min-h-screen">
    <div aria-hidden="true" className="animate-spin rounded-full h-10 w-10 border-2 border-[#dcd5c7] border-t-[#7a3b2c]"></div>
  </div>
);

export function AppRoutes() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/register" element={<Register />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/items"
          element={
            <ProtectedRoute>
              <ItemsCatalog />
            </ProtectedRoute>
          }
        />
        <Route
          path="/items/:id"
          element={
            <ProtectedRoute>
              <ItemDetail />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
