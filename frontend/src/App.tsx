import { BrowserRouter, useLocation } from 'react-router';
import { useEffect, useRef } from 'react';
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

function useScrollRestoration() {
  const location = useLocation();
  const scrollPositions = useRef(new Map<string, number>());
  const isPopState = useRef(false);

  useEffect(() => {
    const onPopState = () => {
      isPopState.current = true;
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  useEffect(() => {
    const pathname = location.pathname;
    const onScroll = () => {
      // Drop transitional scroll events fired while the browser restores the
      // previous page (they belong to the old content, not the new pathname)
      if (isPopState.current) return;
      scrollPositions.current.set(pathname, window.scrollY);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [location.pathname]);

  useEffect(() => {
    if (isPopState.current) {
      isPopState.current = false;
      const saved = scrollPositions.current.get(location.pathname);
      if (saved) {
        // The route's data loads async; retry until the page settles so the
        // restore is not clamped against the transitional DOM height
        let attempts = 0;
        const restore = () => {
          window.scrollTo(0, saved);
          if (++attempts < 8 && Math.abs(window.scrollY - saved) > 2) {
            setTimeout(restore, 120);
          }
        };
        restore();
      }
    } else {
      window.scrollTo(0, 0);
    }
  }, [location.pathname]);
}

function RouteErrorBoundary() {
  useScrollRestoration();
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
      <div className="min-h-screen bg-paper text-ink">
        <Navbar />
        <RouteErrorBoundary />
        <Toaster
          position="top-right"
          toastOptions={{
            duration: 5000,
            style: {
              background: 'var(--color-paper)',
              color: 'var(--color-ink)',
              border: '1px solid var(--color-hair)',
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