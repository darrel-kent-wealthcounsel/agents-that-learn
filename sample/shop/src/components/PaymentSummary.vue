<template>
  <div class="payment-summary">Payment total: {{ paymentLabel(order) }}</div>
</template>

<script setup lang="ts">
import { paymentLabel } from '@/utils/paymentFormat'
defineProps<{ order: { total: number } }>()
</script>
