<template>
  <section>
    <PaymentSummary :order="order" />
    <ActionButton label="Pay now" @click="pay" />
  </section>
</template>

<script setup lang="ts">
import PaymentSummary from '@/components/PaymentSummary.vue'
import ActionButton from '@/components/ActionButton.vue'
import { usePaymentFlow } from '@/composables/usePaymentFlow'

const { order, pay } = usePaymentFlow()
</script>
