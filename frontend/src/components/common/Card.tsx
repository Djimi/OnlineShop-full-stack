import type { ReactNode, HTMLAttributes } from 'react';
import { clsx } from 'clsx';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  hoverable?: boolean;
}

export function Card({ children, hoverable = false, className, ...rest }: CardProps) {
  return (
    <div
      className={clsx(
        'card p-6',
        hoverable && 'transform transition-transform duration-300 hover:-translate-y-1',
        className
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
