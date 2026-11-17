import assert from 'assert/strict';
import { randomBytes } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { test } from 'node:test';
import { getIronSession } from 'iron-session';
import { cookieOptions } from '../src/server/session-cookie-options';

function context(cookie = '') {
  const headers = new Map<string, string | string[]>();
  const request = { headers: { cookie } } as IncomingMessage;
  const response = {
    headersSent: false,
    getHeader: (name: string) => headers.get(name.toLowerCase()),
    setHeader: (name: string, value: string | string[]) => headers.set(name.toLowerCase(), value),
  } as unknown as ServerResponse;
  return { request, response, headers };
}

test('saving a session still works after more than 14 days of process uptime', async (t) => {
  const password = randomBytes(32).toString('hex');
  const { request, response, headers } = context();
  const session = await getIronSession<{ id: string }>(request, response, {
    password,
    cookieName: 'zipline_session',
    cookieOptions: { ...cookieOptions, secure: true },
  });
  session.id = 'synthetic-user';

  t.mock.timers.enable({ apis: ['Date'], now: Date.now() + 21 * 86400 * 1000 });
  await session.save();
  const header = String(headers.get('set-cookie'));
  assert.match(header, /Max-Age=1209600/i);
  assert.match(header, /HttpOnly/i);
  assert.match(header, /Secure/i);
  assert.match(header, /SameSite=Lax/i);

  const next = context(header.split(';')[0]);
  const restored = await getIronSession<{ id: string }>(next.request, next.response, {
    password,
    cookieName: 'zipline_session',
    cookieOptions: { ...cookieOptions, secure: true },
  });
  assert.equal(restored.id, session.id);

  t.mock.timers.tick(15 * 86400 * 1000);
  const expired = await getIronSession<{ id: string }>(next.request, next.response, {
    password,
    cookieName: 'zipline_session',
    cookieOptions: { ...cookieOptions, secure: true },
  });
  assert.equal(expired.id, undefined);
});
