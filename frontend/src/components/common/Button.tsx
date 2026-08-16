import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from 'react-router';
import { clsx } from 'clsx';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: 'primary' | 'secondary' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  fullWidth?: boolean;
  href?: string;
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
  href,
  ...rest
}: ButtonProps) {
  const variantClasses = {
    primary: 'btn btn-primary',
    secondary: 'btn btn-secondary',
    danger: 'btn btn-danger',
  };

  const sizeClasses = {
    sm: 'px-3 py-1.5 text-sm min-h-[44px]',
    md: 'px-4 py-2 text-base min-h-[44px]',
    lg: 'px-6 py-3 text-lg min-h-[44px]',
  };

  const classes = clsx(
    variantClasses[variant],
    sizeClasses[size],
    fullWidth && 'w-full',
    isLoading && 'opacity-50 cursor-not-allowed',
    className
  );

  const content = isLoading ? (
    <span className="flex items-center justify-center gap-2">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"></span>
      Loading...
    </span>
  ) : (
    children
  );

  if (href) {
    return (
      <Link to={href} className={classes} aria-busy={isLoading || undefined} {...(rest as Record<string, unknown>)}>
        {content}
      </Link>
    );
  }

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      {...rest}
    >
      {content}
    </button>
  );
}