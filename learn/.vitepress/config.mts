import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

// Set BASE=/etalase/ when publishing to GitHub Pages under a repo name.
const base = process.env.BASE || '/'

export default withMermaid(defineConfig({
  title: 'Etalase Academy',
  description: 'Learn Etalase from zero to master: agentic commerce, the codebase, security, deployment and the pitch.',
  base,
  lang: 'en-US',
  cleanUrls: true,
  lastUpdated: false,
  srcExclude: ['README.md'],
  themeConfig: {
    nav: [
      { text: 'Start here', link: '/guide/start-here' },
      { text: 'Architecture', link: '/architecture/overview' },
      { text: 'Evidence', link: '/business/evidence' },
      { text: 'Pitch & Q&A', link: '/business/pitch-and-qa' },
    ],
    sidebar: [
      { text: 'Start here', items: [
        { text: 'How to use this guide', link: '/guide/start-here' },
      ]},
      { text: 'Level 1 · Foundations', collapsed: false, items: [
        { text: 'The problem', link: '/foundations/problem' },
        { text: 'Agentic commerce 101', link: '/foundations/agentic-commerce' },
        { text: 'The Etalase product', link: '/foundations/product' },
        { text: 'Glossary', link: '/foundations/glossary' },
      ]},
      { text: 'Level 2 · Run it', collapsed: false, items: [
        { text: 'Run the demo locally', link: '/run/run-locally' },
        { text: 'Hands-on walkthrough', link: '/run/hands-on' },
      ]},
      { text: 'Level 3 · How it works', collapsed: false, items: [
        { text: 'Architecture overview', link: '/architecture/overview' },
        { text: 'Data model and store', link: '/architecture/data-model' },
        { text: 'Catalog and stock', link: '/architecture/catalog-and-stock' },
        { text: 'Checkout and orders', link: '/architecture/checkout-and-orders' },
        { text: 'Agent gateway (MCP)', link: '/architecture/agent-gateway' },
        { text: 'UCP checkout', link: '/architecture/ucp' },
        { text: 'WhatsApp seller agent', link: '/architecture/seller-agent' },
        { text: 'Integrations', link: '/architecture/integrations' },
        { text: 'Security model', link: '/architecture/security' },
      ]},
      { text: 'Level 4 · Ship it', collapsed: false, items: [
        { text: 'Quick setup: only the keys', link: '/ship/quick-setup' },
        { text: 'Testing', link: '/ship/testing' },
        { text: 'Deploy to Cloud Run', link: '/ship/deploy' },
        { text: 'Turn on real services', link: '/ship/real-services' },
        { text: 'WhatsApp templates', link: '/ship/whatsapp-templates' },
      ]},
      { text: 'Level 5 · Business and pitch', collapsed: false, items: [
        { text: 'Business model and costs', link: '/business/business-model' },
        { text: 'Evidence and sources', link: '/business/evidence' },
        { text: 'Pitch and Q&A playbook', link: '/business/pitch-and-qa' },
      ]},
      { text: 'Level 6 · Mastery', collapsed: false, items: [
        { text: 'Exercises', link: '/master/exercises' },
        { text: 'Known gaps and open problems', link: '/master/known-gaps' },
        { text: 'Final quiz', link: '/master/quiz' },
      ]},
    ],
    outline: { level: [2, 3] },
    search: { provider: 'local' },
    footer: { message: 'Etalase · AI Builder Cup 2026 · MIT License' },
  },
  mermaid: {},
}))
