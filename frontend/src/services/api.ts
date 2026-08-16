import axios, { AxiosError } from 'axios';
import type { AxiosInstance } from 'axios';
import toast from 'react-hot-toast';
import { useAuthStore } from '../store/authStore';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000';

const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor to add auth token
api.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().token;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle errors
api.interceptors.response.use(
  (response) => {
    return response;
  },
  (error: AxiosError) => {
    // Handle 401 Unauthorized - redirect to login
    const requestUrl = error.config?.url ?? '';
    const isPublicAuthRequest = /^\/auth\/(login|register)$/.test(requestUrl);

    if (error.response?.status === 401 && !isPublicAuthRequest) {
      useAuthStore.getState().logout();
      sessionStorage.setItem(
        'onlineshop_redirect_from',
        `${window.location.pathname}${window.location.search}${window.location.hash}`
      );
      window.location.href = '/login';
      // Page is about to unload; suppress downstream error handling
      return Promise.reject(new axios.CanceledError('Redirecting to login'));
    }

    if (error.response?.status === 429) {
      const hadToken = error.config?.headers?.has('Authorization');
      if (hadToken) {
        // An authenticated request was throttled - most likely a stale session
        // burning the failed-auth bucket; send the user to login like a 401.
        useAuthStore.getState().logout();
        sessionStorage.setItem(
          'onlineshop_redirect_from',
          `${window.location.pathname}${window.location.search}${window.location.hash}`
        );
        window.location.href = '/login';
        return Promise.reject(new axios.CanceledError('Redirecting to login'));
      }
      toast.error('Too many requests. Please wait a moment and try again.');
      return Promise.reject(error);
    }

    // Log error details
    console.error('API Error:', error.response?.data || error.message);

    return Promise.reject(error);
  }
);

export default api;
