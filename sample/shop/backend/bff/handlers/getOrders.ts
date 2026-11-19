import { dalRequest } from '../dalClient'

export const route = { method: 'GET', path: '/orders' }

export async function handler() {
  return dalRequest('GET', '/records/orders')
}
