import type { SessionOptions } from 'iron-session';

// A relative lifetime remains valid after long process uptime. An absolute
// expires Date at module scope eventually prevents iron-session from saving.
export const cookieOptions: NonNullable<SessionOptions['cookieOptions']> = {
  maxAge: 60 * 60 * 24 * 14,
  path: '/',
  sameSite: 'lax',
  httpOnly: true,
};
