import axios from 'axios';

type ProblemDetails = {
  detail?: string;
};

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<ProblemDetails>(error)) {
    return error.response?.data?.detail ?? error.message ?? fallback;
  }

  return error instanceof Error ? error.message : fallback;
}
