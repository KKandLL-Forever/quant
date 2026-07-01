export type ErrorSeverity = 'info' | 'warn' | 'error';
export type ErrorKind =
  | 'parse'
  | 'validation'
  | 'network'
  | 'api'
  | 'storage'
  | 'unknown';

export interface AppErrorOptions {
  kind: ErrorKind;
  message: string;
  detail?: string;
  context?: Record<string, unknown>;
  severity?: ErrorSeverity;
  sticky?: boolean;
  cause?: unknown;
}

let _idCounter = 0;
function nextId(): string {
  _idCounter += 1;
  return `${Date.now().toString(36)}-${_idCounter.toString(36)}`;
}

export class AppError extends Error {
  readonly id: string;
  readonly kind: ErrorKind;
  readonly detail?: string;
  readonly context?: Record<string, unknown>;
  readonly severity: ErrorSeverity;
  readonly sticky: boolean;
  readonly timestamp: number;

  constructor(opts: AppErrorOptions) {
    super(opts.message);
    this.name = 'AppError';
    this.id = nextId();
    this.kind = opts.kind;
    this.detail = opts.detail;
    this.context = opts.context;
    this.severity = opts.severity ?? 'error';
    this.sticky = opts.sticky ?? false;
    this.timestamp = Date.now();
    if (opts.cause !== undefined) {
      (this as Error & { cause?: unknown }).cause = opts.cause;
    }
  }

  static parse(message: string, detail?: string, cause?: unknown): AppError {
    return new AppError({ kind: 'parse', message, detail, cause });
  }

  static network(message: string, detail?: string, cause?: unknown): AppError {
    return new AppError({ kind: 'network', message, detail, cause });
  }

  static api(message: string, detail?: string, cause?: unknown): AppError {
    return new AppError({ kind: 'api', message, detail, cause });
  }

  static validation(message: string, zodError: unknown): AppError {
    let detail: string | undefined;
    if (zodError && typeof zodError === 'object' && 'message' in zodError) {
      detail = String((zodError as { message: unknown }).message);
    }
    return new AppError({ kind: 'validation', message, detail, cause: zodError });
  }

  static storage(message: string, detail?: string, cause?: unknown): AppError {
    return new AppError({ kind: 'storage', message, detail, cause, sticky: true });
  }

  static from(err: unknown, fallbackKind: ErrorKind = 'unknown'): AppError {
    if (err instanceof AppError) return err;
    if (err instanceof Error) {
      return new AppError({
        kind: fallbackKind,
        message: err.message,
        detail: err.stack,
        cause: err,
      });
    }
    return new AppError({
      kind: fallbackKind,
      message: typeof err === 'string' ? err : 'Unknown error',
      detail: typeof err === 'object' ? JSON.stringify(err) : undefined,
      cause: err,
    });
  }
}
