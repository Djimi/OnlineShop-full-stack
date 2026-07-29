import { BrowserRouter } from 'react-router';
import { Navbar } from './components/layout/Navbar';
import { AppRoutes } from './routes';
import { Toaster } from 'react-hot-toast';
import { useAuthStore } from './store/authStore';

useAuthStore.getState().loadFromStorage();

function App() {

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[#f4f1ea] text-[#1f1a14]">
        <Navbar />
        <AppRoutes />
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
