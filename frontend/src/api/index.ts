import type { ApiClient } from './client';
import { MockApi } from './mockApi';

// VITE_API_MODE=mock (por defecto): datos simulados, sin AWS. La API real se conectará aquí cuando esté desplegada (y con el token del login).
const mode = import.meta.env.VITE_API_MODE ?? 'mock';
if (mode !== 'mock') throw new Error(`VITE_API_MODE=${mode}: la API real aún no está conectada. Usa "mock".`);

export const api: ApiClient = new MockApi();
