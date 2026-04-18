import { request } from './_request';

export const login = (password: string) =>
  request('/auth/login', { method: 'POST', body: JSON.stringify({ password }) });

export const logout = () =>
  request('/auth/logout', { method: 'POST' });
