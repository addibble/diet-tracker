import { request } from './_request';
import {
  startRegistration,
  startAuthentication,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';

export interface MePasskey {
  id: number;
  nickname: string | null;
  created_at: string;
  last_used_at: string | null;
  aaguid: string | null;
}

export interface MeSession {
  token_hash: string;
  created_at: string;
  expires_at: string;
  last_seen_at: string;
  user_agent: string | null;
  ip: string | null;
}

export interface Me {
  user: {
    id: string;
    email: string;
    display_name: string;
    is_admin: boolean;
  };
  passkeys: MePasskey[];
  sessions: MeSession[];
}

export const me = (): Promise<Me> => request('/auth/me');

export const logout = () => request('/auth/logout', { method: 'POST' });

/**
 * Register a new user account via invite. The browser prompts for a passkey
 * and the server issues a session cookie on success.
 */
export async function registerWithInvite(
  inviteToken: string,
  email: string,
  displayName: string,
): Promise<Me> {
  const opts = await request<{
    challenge_id: number;
    publicKey: PublicKeyCredentialCreationOptionsJSON;
  }>('/auth/register/options', {
    method: 'POST',
    body: JSON.stringify({
      invite_token: inviteToken,
      email,
      display_name: displayName,
    }),
  });
  const cred = await startRegistration({ optionsJSON: opts.publicKey });
  await request('/auth/register/verify', {
    method: 'POST',
    body: JSON.stringify({ challenge_id: opts.challenge_id, credential: cred }),
  });
  return me();
}

/** Prompt the browser to sign with an existing passkey for `email`. */
export async function loginWithPasskey(email: string): Promise<Me> {
  const opts = await request<{
    challenge_id: number;
    publicKey: PublicKeyCredentialRequestOptionsJSON;
  }>('/auth/login/options', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
  const cred = await startAuthentication({ optionsJSON: opts.publicKey });
  await request('/auth/login/verify', {
    method: 'POST',
    body: JSON.stringify({ challenge_id: opts.challenge_id, credential: cred }),
  });
  return me();
}

/** Add an additional passkey to the logged-in account. */
export async function addPasskey(nickname?: string): Promise<void> {
  const opts = await request<{
    challenge_id: number;
    publicKey: PublicKeyCredentialCreationOptionsJSON;
  }>('/auth/passkeys/options', {
    method: 'POST',
    body: JSON.stringify({ nickname: nickname ?? null }),
  });
  const cred = await startRegistration({ optionsJSON: opts.publicKey });
  await request('/auth/passkeys/verify', {
    method: 'POST',
    body: JSON.stringify({
      challenge_id: opts.challenge_id,
      credential: cred,
      nickname: nickname ?? null,
    }),
  });
}

export const deletePasskey = (id: number) =>
  request(`/auth/passkeys/${id}`, { method: 'DELETE' });

export const revokeSession = (tokenHash: string) =>
  request(`/auth/sessions/${encodeURIComponent(tokenHash)}`, { method: 'DELETE' });

