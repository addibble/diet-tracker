export const BASE = '/api';
const MAX_IMPORT_IMAGE_BYTES = 1_500_000;
const MAX_IMPORT_IMAGE_DIMENSION = 1600;

import { recordEvent } from '../lib/telemetry';

function normalizeServerErrorText(status: number, rawText: string): string {
  const normalized = rawText.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  const lower = normalized.toLowerCase();
  const isCloudflareErrorPage = lower.includes('cloudflare') && (
    lower.includes('bad gateway')
    || lower.includes('host error')
    || lower.includes('gateway timeout')
  );

  if (status >= 500 && isCloudflareErrorPage) {
    return (
      'Gateway error between Cloudflare and the app/model provider. '
      + 'Retry in a minute or switch models.'
    );
  }

  return normalized;
}

function blobToFile(blob: Blob, originalName: string): File {
  const baseName = originalName.replace(/\.[^.]+$/, '') || 'upload';
  return new File([blob], `${baseName}.jpg`, { type: 'image/jpeg' });
}

function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  const objectUrl = URL.createObjectURL(file);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error('Could not read image'));
    };
    img.src = objectUrl;
  });
}

function canvasToBlob(
  canvas: HTMLCanvasElement,
  quality: number,
): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), 'image/jpeg', quality);
  });
}

export async function optimizeImageForUpload(file: File): Promise<File> {
  if (!file.type.startsWith('image/')) return file;
  if (file.size <= MAX_IMPORT_IMAGE_BYTES) return file;

  try {
    const img = await loadImageFromFile(file);
    const maxDim = Math.max(img.naturalWidth, img.naturalHeight);
    const scale = maxDim > MAX_IMPORT_IMAGE_DIMENSION ? MAX_IMPORT_IMAGE_DIMENSION / maxDim : 1;

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));

    const ctx = canvas.getContext('2d');
    if (!ctx) return file;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    const qualityCandidates = [0.82, 0.72, 0.62, 0.52];
    let bestBlob: Blob | null = null;

    for (const quality of qualityCandidates) {
      const blob = await canvasToBlob(canvas, quality);
      if (!blob) continue;
      bestBlob = blob;
      if (blob.size <= MAX_IMPORT_IMAGE_BYTES) {
        return blobToFile(blob, file.name);
      }
    }

    if (!bestBlob) return file;
    if (bestBlob.size < file.size) return blobToFile(bestBlob, file.name);
    return file;
  } catch {
    return file;
  }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const isFormData = options?.body instanceof FormData;
  const headers = new Headers(options?.headers ?? {});
  if (!isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const method = (options?.method ?? 'GET').toUpperCase();
  // Skip self-instrumentation of telemetry posts to avoid feedback loops.
  const skipTelemetry = path.startsWith('/telemetry/');
  const t0 = performance.now();
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers,
  });
  if (!skipTelemetry) {
    const duration = performance.now() - t0;
    // Coarsen path so the backend aggregation doesn't blow up on every id.
    const apiPath = path.split('?')[0].replace(/\/\d+/g, '/:id');
    const serverTiming = res.headers.get('Server-Timing') || undefined;
    recordEvent({
      name: `api:${method} ${apiPath}`,
      duration_ms: duration,
      meta: {
        status: res.status,
        server_timing: serverTiming,
      },
    });
  }
  const errorDetail = await readErrorDetail(res);
  if (errorDetail) {
    throw new Error(errorDetail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function readErrorDetail(res: Response): Promise<string | null> {
  if (res.status === 401) {
    window.location.href = '/login';
    return 'Unauthorized';
  }
  if (!res.ok) {
    if (res.status === 413) {
      return 'Image is too large. Please retake closer or crop the photo.';
    }

    let detail = '';
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const err = await res.json().catch(() => ({ detail: '' }));
      detail = String(err.detail || '');
    } else {
      const text = await res.text().catch(() => '');
      detail = normalizeServerErrorText(res.status, text);
    }

    return detail || res.statusText || 'Request failed';
  }
  return null;
}
