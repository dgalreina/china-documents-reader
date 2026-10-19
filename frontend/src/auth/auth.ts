import { AuthenticationDetails, CognitoUser, CognitoUserPool, type CognitoUserSession } from 'amazon-cognito-identity-js';
import { friendlyAuthError } from './errors';

/**
 * El login con Cognito (un solo usuario, sin registro abierto). `amazon-cognito-identity-js` hace el login SRP: la contraseña no viaja, y renueva el
 * token solo mientras dure la sesión. Se usa el token de ID: es el que la API valida (su "audiencia" es el cliente de esta web).
 */
export class AuthService {
  private pool: CognitoUserPool;

  constructor(config: { userPoolId: string; userPoolClientId: string }) {
    this.pool = new CognitoUserPool({ UserPoolId: config.userPoolId, ClientId: config.userPoolClientId });
  }

  private session(): Promise<CognitoUserSession> {
    return new Promise((resolve, reject) => {
      const user = this.pool.getCurrentUser();
      if (!user) return reject(new Error('no-session'));
      // getSession renueva el token con el refresh token si hace falta.
      user.getSession((error: Error | null, session: CognitoUserSession | null) => {
        if (error || !session || !session.isValid()) reject(error ?? new Error('no-session'));
        else resolve(session);
      });
    });
  }

  /** ¿Había ya una sesión válida guardada de otra visita? */
  async restore(): Promise<boolean> {
    try {
      await this.session();
      return true;
    } catch {
      return false;
    }
  }

  signIn(email: string, password: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const username = email.trim();
      const user = new CognitoUser({ Username: username, Pool: this.pool });
      user.authenticateUser(new AuthenticationDetails({ Username: username, Password: password }), {
        onSuccess: () => resolve(),
        onFailure: (error: unknown) => reject(new Error(friendlyAuthError(error))),
        newPasswordRequired: () => reject(new Error('Esta cuenta tiene que cambiar la contraseña, y la web aún no lo permite.')),
        mfaRequired: () => reject(new Error('Esta cuenta pide un segundo factor, y la web aún no lo admite.')),
        totpRequired: () => reject(new Error('Esta cuenta pide un segundo factor, y la web aún no lo admite.')),
      });
    });
  }

  async getIdToken(): Promise<string> {
    return (await this.session()).getIdToken().getJwtToken();
  }

  signOut(): void {
    this.pool.getCurrentUser()?.signOut();
  }
}
