import { defineConfig } from 'vite';

export default defineConfig({
  base: process.env.GITHUB_ACTIONS ? '/My_favorite_KBO_team_Lotto/' : '/',
});
