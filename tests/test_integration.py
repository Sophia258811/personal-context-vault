import unittest
from pcv.integration import integrate


class IntegrationTests(unittest.TestCase):
    def test_content_identity_observations_and_authorization_boundaries(self):
        first = dict(observation_id='1', platform='video-site', source_id=1,
                     platform_content_id='video-7', available_modalities=['text'], position=12)
        later = dict(first, observation_id='2', position=27, available_modalities=['image'])
        groups = integrate([first, first, later])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]['observations']), 2)
        self.assertEqual(groups[0]['available_modalities'], ['image', 'text'])
        self.assertEqual(len(integrate([first, dict(first, source_id=2)])), 2)
        self.assertEqual(len(integrate([first, dict(first, platform='other-site')])), 2)
        unknown = dict(first, platform_content_id=None, url='https://example.org/')
        groups = integrate([unknown, dict(unknown, observation_id='2')])
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(g['identity_status'] == 'unresolved' for g in groups))
