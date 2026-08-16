import { Link, useNavigate } from 'react-router';
import { useAuthStore } from '../../store/authStore';

export function Navbar() {
  const navigate = useNavigate();
  const { isAuthenticated, username, logout } = useAuthStore();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  return (
    <nav aria-label="Primary" className="flex items-center gap-4 px-6 md:px-16 py-6 md:py-8 border-b border-[#dcd5c7] md:grid md:grid-cols-[1fr_auto_1fr]">
      <Link to="/" className="shrink-0 md:justify-self-start">
        <span className="font-display text-2xl tracking-[0.01em]">
          Online<em className="italic text-[#7a3b2c]">shop</em>
        </span>
      </Link>

      <div className="hidden md:flex gap-10 justify-self-center">
        <Link to="/items" className="nav-link">Shop</Link>
      </div>

      <div className="flex gap-2 sm:gap-6 items-center ml-auto md:justify-self-end md:ml-0">
        {isAuthenticated ? (
          <>
            <span className="hidden sm:inline font-display italic text-[#5b524a] text-base">
              Welcome,&nbsp;<span className="text-[#1f1a14]">{username}</span>
            </span>
            <button type="button" onClick={handleLogout} className="nav-link hover:text-[#7a3b2c]">
              Sign&nbsp;out
            </button>
          </>
        ) : (
          <>
            <Link to="/login" className="btn btn-primary px-3 py-2 sm:px-5 sm:py-3">
              Sign in
            </Link>
            <Link to="/register" aria-label="Create account" className="btn btn-primary px-3 py-2 sm:px-5 sm:py-3">
              <span className="sm:hidden">Join</span>
              <span className="hidden sm:inline">Create account</span>
            </Link>
          </>
        )}
      </div>
    </nav>
  );
}
