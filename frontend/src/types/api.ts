// Auth Types
export interface RegisterRequest {
  username: string;
  password: string;
}

export interface RegisterResponse {
  userId: number;
  username: string;
  createdAt: string;
  updatedAt: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  token: string;
  userId: number;
  username: string;
  createdAt: string;
  expiresAt: string;
  tokenType: string;
  expiresIn: number;
}

export interface ValidateResponse {
  valid: boolean;
  userId: number;
  username: string;
  createdAt: string;
  expiresAt: string;
}

export interface ErrorResponse {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
}

// Items Types
export interface ItemDTO {
  id: string;
  name: string;
  quantity: number;
  description: string;
}

// Store Types
export interface AuthState {
  token: string | null;
  userId: number | null;
  username: string | null;
  isAuthenticated: boolean;
  setAuth: (token: string, userId: number, username: string) => void;
  logout: () => void;
  loadFromStorage: () => void;
}
