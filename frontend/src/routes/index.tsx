import { Routes, Route, Navigate } from 'react-router';
import { ProtectedRoute } from './ProtectedRoute';
import Home from '../pages/Home';
import Register from '../pages/Register';
import Login from '../pages/Login';
import ItemsCatalog from '../pages/ItemsCatalog';
import ItemDetail from '../pages/ItemDetail';

export function AppRoutes() {
  return (
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
  );
}