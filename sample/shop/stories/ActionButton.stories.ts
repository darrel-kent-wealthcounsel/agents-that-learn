import { fn } from 'storybook/test'
import ActionButton from '../src/components/ActionButton.vue'

export default { component: ActionButton }
export const Primary = { args: { label: 'Pay now', onClick: fn() } }
