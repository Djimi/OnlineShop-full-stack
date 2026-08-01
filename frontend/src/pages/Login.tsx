import { useNavigate, Link } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '../components/common/Button';
import { Input } from '../components/common/Input';
import { authService } from '../services/authService';
import toast from 'react-hot-toast';
import { useState } from 'react';
import { useAuthStore } from '../store/authStore';

const loginSchema = z.object({
  username: z.string().min(1, 'Username is required'),
  password: z.string().min(1, 'Password is required'),
});

type LoginFormData = z.infer<typeof loginSchema>;

export default function Login() {
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);
  const setAuth = useAuthStore((state) => state.setAuth);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  });

  const onSubmit = async (data: LoginFormData) => {
    setIsLoading(true);
    try {
      const response = await authService.login({
        username: data.username,
        password: data.password,
      });

      setAuth(response.token, response.userId, response.username);
      toast.success(`Welcome back, ${response.username}.`);
      setTimeout(() => navigate('/items', { replace: true }), 500);
    } catch (error: any) {
      const errorMessage =
        error.response?.data?.detail ||
        error.message ||
        'Login failed. Please check your credentials.';
      toast.error(errorMessage);
      console.error('Login error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-[calc(100vh-120px)] flex items-center justify-center px-6 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-10">
          <div className="w-px h-14 bg-[#1f1a14] opacity-40 mx-auto mb-6" />
          <div className="eyebrow mb-5">— Sign in —</div>
          <h1 className="font-display font-light text-5xl leading-[1] tracking-[-0.015em]">
            Welcome <em className="italic text-[#7a3b2c] font-normal">back</em>.
          </h1>
          <p className="mt-5 text-[#5b524a] text-sm font-light">
            Sign in to continue to your shelf.
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-7">
          <Input
            label="Username"
            placeholder="your username"
            error={errors.username?.message}
            {...register('username')}
          />
          <Input
            label="Password"
            type="password"
            placeholder="your password"
            error={errors.password?.message}
            {...register('password')}
          />

          <div className="pt-2">
            <Button variant="primary" size="lg" fullWidth type="submit" isLoading={isLoading}>
              Sign in
            </Button>
          </div>
        </form>

        <p className="text-center mt-10 text-sm text-[#5b524a] font-light">
          New here?{' '}
          <Link to="/register" className="text-[#7a3b2c] border-b border-[#7a3b2c] pb-0.5 hover:text-[#1f1a14] hover:border-[#1f1a14]">
            Create an account
          </Link>
        </p>
      </div>
    </div>
  );
}
