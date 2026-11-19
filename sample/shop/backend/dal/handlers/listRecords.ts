import { selectRows } from '../db'

export const route = { method: 'GET', path: '/records/orders' }

export async function handler() {
  return selectRows('orders')
}
