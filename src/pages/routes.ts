import { index, prefix, route, type RouteConfig } from '@react-router/dev/routes'

export default [
  index('./home/page.tsx'),
  route('about', './about/page.tsx'),
  route('freeplay', './freeplay/page.tsx'),
  route('play', './play/page.tsx'),
  route('account/:userId', './account/page.tsx'),
  route('account', './account/redirect.tsx'),
  route('recordings/:userId', './recordings/page.tsx'),
  route('recordings', './recordings/redirect.tsx'),
  route('challenge-songs/:userId', './challenge-songs/page.tsx'),
  route('challenge-songs', './challenge-songs/redirect.tsx'),
  route('challenge', './challenge/page.tsx'),
  route('songs', './songs/page.tsx'),
  route('login', './login/page.tsx'),
  route('register', './register/page.tsx'),
  ...prefix('training', [
    route('phrases', './training/phrases/page.tsx'),
    route('phrases', './training/speed/page.tsx'),
  ]),
] satisfies RouteConfig
