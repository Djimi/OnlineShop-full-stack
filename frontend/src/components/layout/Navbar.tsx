import { Link, useNavigate } from 'react-router';
import { useAuthStore } from '../../store/authStore';
import { Button } from '../common/Button';

export function Navbar() {
  const navigate = useNavigate();
  const { isAuthenticated, username, logout } = useAuthStore();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  return (
    <nav aria-label="Primary" className="flex items-center gap-4 px-6 md:px-16 py-6 md:py-8 border-b border-hair md:grid md:grid-cols-[1fr_auto_1fr]">
      <Link
        to="/"
        className="shrink-0 md:justify-self-start transition-opacity duration-200 hover:opacity-70"
      >
        <span className="font-display text-2xl tracking-[0.01em]">
          Online<em className="italic text-accent">shop</em>
        </span>
      </Link>

      <div className="hidden md:flex gap-10 justify-self-center">
        <Link to="/items" className="nav-link">Shop</Link>
      </div>

      <div className="flex gap-2 sm:gap-6 items-center ml-auto md:justify-self-end md:ml-0">
        {isAuthenticated ? (
          <>
            <span className="hidden sm:inline font-display italic text-soft text-base">
              Welcome,&nbsp;<span className="text-ink">{username}</span>
            </span>
            <button type="button" onClick={handleLogout} className="nav-link hover:text-accent py-2">
              Sign&nbsp;out
            </button>
          </>
        ) : (
          <>
            <Button href="/login" size="md">
              Sign in
            </Button>
            <Button href="/register" size="md" aria-label="Create account">
              <span className="sm:hidden">Join</span>
              <span className="hidden sm:inline">Create account</span>
            </Button>
          </>
        )}
      </div>
    </nav>
  );
}