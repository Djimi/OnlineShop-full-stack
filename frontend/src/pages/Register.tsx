import { useNavigate, Link } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '../components/common/Button';
import { Input } from '../components/common/Input';
import { authService } from '../services/authService';
import toast from 'react-hot-toast';
import { useState } from 'react';
import { getApiErrorMessage } from '../utils/apiError';

const registerSchema = z
  .object({
    username: z
      .string()
      .min(3, 'Username must be at least 3 characters')
      .max(50, 'Username must be less than 50 characters'),
    password: z
      .string()
      .min(6, 'Password must be at least 6 characters')
      .max(100, 'Password must be less than 100 characters'),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: 'Passwords do not match',
    path: ['confirmPassword'],
  });

type RegisterFormData = z.infer<typeof registerSchema>;

export default function Register() {
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterFormData>({
    resolver: zodResolver(registerSchema),
  });

  const onSubmit = async (data: RegisterFormData) => {
    setIsLoading(true);
    try {
      const response = await authService.register({
        username: data.username,
        password: data.password,
      });

      toast.success(`Welcome ${response.username}. Please sign in.`);
      setTimeout(() => navigate('/login'), 2000);
    } catch (error: unknown) {
      const errorMessage = getApiErrorMessage(error, 'Registration failed. Please try again.');
      toast.error(errorMessage);
      console.error('Registration error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-[calc(100vh-120px)] flex items-center justify-center px-6 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-10">
          <div className="w-px h-14 bg-[#1f1a14] opacity-40 mx-auto mb-6" />
          <div className="eyebrow mb-5">— Create account —</div>
          <h1 className="font-display font-light text-5xl leading-[1] tracking-[-0.015em]">
            Join the <em className="italic text-[#7a3b2c] font-normal">edition</em>.
          </h1>
          <p className="mt-5 text-[#5b524a] text-sm font-light">
            A small, seasonal shop. Your shelf awaits.
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-7">
          <Input
            label="Username"
            placeholder="choose a username"
            error={errors.username?.message}
            {...register('username')}
          />
          <Input
            label="Password"
            type="password"
            placeholder="choose a strong password"
            error={errors.password?.message}
            {...register('password')}
          />
          <Input
            label="Confirm password"
            type="password"
            placeholder="re-enter your password"
            error={errors.confirmPassword?.message}
            {...register('confirmPassword')}
          />

          <div className="pt-2">
            <Button variant="primary" size="lg" fullWidth type="submit" isLoading={isLoading}>
              Create account
            </Button>
          </div>
        </form>

        <p className="text-center mt-10 text-sm text-[#5b524a] font-light">
          Already have an account?{' '}
          <Link to="/login" className="text-[#7a3b2c] border-b border-[#7a3b2c] pb-0.5 hover:text-[#1f1a14] hover:border-[#1f1a14]">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
