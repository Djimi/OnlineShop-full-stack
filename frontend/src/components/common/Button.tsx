import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { clsx } from 'clsx';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: 'primary' | 'secondary' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  fullWidth?: boolean;
}

export function Button({
  children,
  variant = 'primary',
  size = 'md',
  isLoading = false,
  fullWidth = false,
  type = 'button',
  className,
  disabled,
  ...rest
}: ButtonProps) {
  const variantClasses = {
    primary: 'btn btn-primary',
    secondary: 'btn btn-secondary',
    danger: 'btn btn-danger',
  };

  const sizeClasses = {
    sm: 'px-3 py-1 text-sm',
    md: 'px-4 py-2 text-base',
    lg: 'px-6 py-3 text-lg',
  };

  return (
    <button
      type={type}
      className={clsx(
        variantClasses[variant],
        sizeClasses[size],
        fullWidth && 'w-full',
        isLoading && 'opacity-50 cursor-not-allowed',
        className
      )}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      {...rest}
    >
      {isLoading ? (
        <span className="flex items-center justify-center gap-2">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"></span>
          Loading...
        </span>
      ) : (
        children
      )}
    </button>
  );
}
