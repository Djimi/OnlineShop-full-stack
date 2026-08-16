import { BrowserRouter, useLocation } from 'react-router';
import { Navbar } from './components/layout/Navbar';
import { AppRoutes } from './routes';
import { Toaster } from 'react-hot-toast';
import { useAuthStore } from './store/authStore';
import { ErrorBoundary } from './components/common/ErrorBoundary';
import { authService } from './services/authService';

useAuthStore.getState().loadFromStorage();

if (useAuthStore.getState().isAuthenticated) {
  authService
    .validate()
    .then((response) => {
      if (!response.valid) {
        useAuthStore.getState().logout();
      }
    })
    .catch(() => {
      // Network errors must not log the user out; the 401 interceptor handles expiry
    });
}

function RouteErrorBoundary() {
  const location = useLocation();
  return (
    <ErrorBoundary key={location.pathname}>
      <AppRoutes />
    </ErrorBoundary>
  );
}

function App() {

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[#f4f1ea] text-[#1f1a14]">
        <Navbar />
        <RouteErrorBoundary />
        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: '#f4f1ea',
              color: '#1f1a14',
              border: '1px solid #dcd5c7',
              borderRadius: 0,
              fontSize: '13px',
              letterSpacing: '0.02em',
            },
          }}
        />
      </div>
    </BrowserRouter>
  );
}

export default App;
