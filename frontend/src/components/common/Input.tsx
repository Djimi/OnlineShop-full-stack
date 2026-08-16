import type { InputHTMLAttributes } from 'react';
import { forwardRef, useId } from 'react';
import { clsx } from 'clsx';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helpText?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, helpText, className, id, ...rest }, ref) => {
    const generatedId = useId();
    const inputId = id ?? generatedId;
    const describedBy = error
      ? `${inputId}-error`
      : helpText
        ? `${inputId}-help`
        : undefined;

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="form-label">
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={clsx(
            'input-field',
            error && 'border-b-[#7a3b2c] focus:border-b-[#7a3b2c]',
            className
          )}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
        />
        {error && <p id={`${inputId}-error`} role="alert" className="form-error">{error}</p>}
        {helpText && !error && <p id={`${inputId}-help`} className="text-[#5b524a] text-sm mt-1">{helpText}</p>}
      </div>
    );
  }
);

Input.displayName = 'Input';
