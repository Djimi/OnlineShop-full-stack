import { create } from 'zustand';
import type { AuthState } from '../types/api';

const STORAGE_KEY = 'onlineshop_auth';

type StoredAuth = {
  token: string;
  userId: number;
  username: string;
};

function isStoredAuth(value: unknown): value is StoredAuth {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const authData = value as Partial<StoredAuth>;
  return (
    typeof authData.token === 'string' && authData.token.length > 0 &&
    typeof authData.userId === 'number' && Number.isFinite(authData.userId) &&
    typeof authData.username === 'string' && authData.username.length > 0
  );
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  userId: null,
  username: null,
  isAuthenticated: false,

  setAuth: (token: string, userId: number, username: string) => {
    const authData = { token, userId, username };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(authData));
    set({
      token,
      userId,
      username,
      isAuthenticated: true,
    });
  },

  logout: () => {
    localStorage.removeItem(STORAGE_KEY);
    set({
      token: null,
      userId: null,
      username: null,
      isAuthenticated: false,
    });
  },

  loadFromStorage: () => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return;
    }

    try {
      const authData: unknown = JSON.parse(stored);
      if (!isStoredAuth(authData)) {
        localStorage.removeItem(STORAGE_KEY);
        return;
      }

      set({
        token: authData.token,
        userId: authData.userId,
        username: authData.username,
        isAuthenticated: true,
      });
    } catch (error) {
      localStorage.removeItem(STORAGE_KEY);
      console.error('Failed to load auth from storage:', error);
    }
  },
}));
