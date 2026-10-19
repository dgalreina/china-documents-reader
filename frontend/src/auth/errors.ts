/** Traduce los errores de Cognito a mensajes claros. Nunca se enseña el texto original (puede dar pistas sobre si un correo existe). */
export function friendlyAuthError(error: unknown): string {
  const code = String((error as { code?: unknown; name?: unknown } | null)?.code ?? (error as { name?: unknown } | null)?.name ?? '');
  const message = String((error as { message?: unknown } | null)?.message ?? '');
  if (code === 'NotAuthorizedException' || code === 'UserNotFoundException') return 'Correo o contraseña incorrectos.';
  if (code === 'UserNotConfirmedException') return 'Esta cuenta aún no está confirmada.';
  if (code === 'PasswordResetRequiredException') return 'Hay que restablecer la contraseña de esta cuenta.';
  if (code === 'TooManyRequestsException' || code === 'LimitExceededException') return 'Demasiados intentos. Espera unos minutos y vuelve a probar.';
  if (code === 'NetworkError' || /network/i.test(message)) return 'No hay conexión. Comprueba tu conexión y vuelve a intentarlo.';
  return 'No se pudo iniciar sesión. Vuelve a intentarlo.';
}
