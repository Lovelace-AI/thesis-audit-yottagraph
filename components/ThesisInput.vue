<template>
    <div class="thesis-input">
        <div class="thesis-header">
            <h1 class="thesis-title">Thesis Audit</h1>
            <p class="thesis-subtitle">
                Propose a financial theory. We'll research it against the yottagraph and report what
                supports or contradicts it.
            </p>
        </div>

        <v-textarea
            v-model="thesisText"
            variant="outlined"
            color="primary"
            rows="4"
            auto-grow
            placeholder='e.g. "When Netflix stock goes up, its competitors&apos; stocks tend to go down in the following months"'
            :disabled="disabled"
            class="thesis-field"
            @keydown.meta.enter="handleSubmit"
            @keydown.ctrl.enter="handleSubmit"
        />

        <div class="thesis-actions">
            <v-btn
                color="primary"
                size="large"
                :disabled="!thesisText.trim() || disabled"
                :loading="disabled"
                @click="handleSubmit"
            >
                Research This Thesis
            </v-btn>
        </div>

        <div v-if="!disabled" class="thesis-examples">
            <span class="examples-label">Try an example:</span>
            <v-chip
                v-for="example in examples"
                :key="example"
                variant="outlined"
                size="small"
                class="example-chip"
                @click="thesisText = example"
            >
                {{ example }}
            </v-chip>
        </div>
    </div>
</template>

<script setup lang="ts">
    const thesisText = defineModel<string>('modelValue', { default: '' });

    defineProps<{
        disabled?: boolean;
    }>();

    const emit = defineEmits<{
        submit: [thesis: string];
    }>();

    const examples = [
        'When Netflix stock rises, Disney and Paramount stocks tend to fall within 3 months',
        'Banks with high exposure to commercial real estate are more likely to face regulatory action',
        'Companies that announce major layoffs see their stock price increase in the short term',
        'Rising federal funds rate correlates with declining tech sector valuations',
    ];

    function handleSubmit() {
        if (thesisText.value.trim()) {
            emit('submit', thesisText.value.trim());
        }
    }
</script>

<style scoped>
    .thesis-input {
        max-width: 720px;
        width: 100%;
        margin: 0 auto;
    }

    .thesis-header {
        text-align: center;
        margin-bottom: 32px;
    }

    .thesis-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 2.2rem;
        letter-spacing: 0.02em;
        margin-bottom: 8px;
    }

    .thesis-subtitle {
        color: var(--lv-silver);
        font-size: 1rem;
        line-height: 1.5;
    }

    .thesis-field {
        margin-bottom: 8px;
    }

    .thesis-actions {
        display: flex;
        justify-content: center;
        margin-bottom: 24px;
    }

    .thesis-examples {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
    }

    .examples-label {
        font-family: var(--font-mono);
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .example-chip {
        cursor: pointer;
    }
</style>
