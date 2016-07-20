"""Test main execution."""

import os
import logging

from contextlib import ExitStack
from io import StringIO
from pkg_resources import resource_filename
from tempfile import TemporaryDirectory
from ubuntu_image.__main__ import main
from ubuntu_image.builder import ModelAssertionBuilder
from ubuntu_image.tests.test_builder import XXXModelAssertionBuilder
from unittest import TestCase, skipIf
from unittest.mock import call, patch


IN_TRAVIS = 'IN_TRAVIS' in os.environ


class CrashingModelAssertionBuilder(XXXModelAssertionBuilder):
    def make_temporary_directories(self):
        raise RuntimeError


class EarlyExitModelAssertionBuilder(XXXModelAssertionBuilder):
    def populate_rootfs_contents(self):
        # Do nothing, but let the state machine exit.
        pass


class DoNothingBuilder(XXXModelAssertionBuilder):
    def populate_rootfs_contents(self):
        self._next.append(self.calculate_rootfs_size)

    def populate_bootfs_contents(self):
        self._next.append(self.calculate_bootfs_size)


class TestMain(TestCase):
    def setUp(self):
        super().setUp()
        self._resources = ExitStack()
        self.addCleanup(self._resources.close)
        # Capture builtin print() output.
        self._stdout = StringIO()
        self._stderr = StringIO()
        self._resources.enter_context(
            patch('argparse._sys.stdout', self._stdout))
        # Capture stderr since this is where argparse will spew to.
        self._resources.enter_context(
            patch('argparse._sys.stderr', self._stderr))

    def test_help(self):
        with self.assertRaises(SystemExit) as cm:
            main(('--help',))
        self.assertEqual(cm.exception.code, 0)
        lines = self._stdout.getvalue().splitlines()
        self.assertTrue(lines[0].startswith('usage: ubuntu-image'),
                        lines[0])

    def test_debug(self):
        with ExitStack() as resources:
            mock = resources.enter_context(
                patch('ubuntu_image.__main__.logging.basicConfig'))
            resources.enter_context(patch(
                'ubuntu_image.__main__.ModelAssertionBuilder',
                EarlyExitModelAssertionBuilder))
            # Prevent actual main() from running.
            resources.enter_context(patch('ubuntu_image.__main__.main'))
            code = main(('--debug', 'model.assertion'))
        self.assertEqual(code, 0)
        mock.assert_called_once_with(level=logging.DEBUG)

    def test_no_debug(self):
        with ExitStack() as resources:
            mock = resources.enter_context(
                patch('ubuntu_image.__main__.logging.basicConfig'))
            resources.enter_context(patch(
                'ubuntu_image.__main__.ModelAssertionBuilder',
                EarlyExitModelAssertionBuilder))
            # Prevent actual main() from running.
            resources.enter_context(patch('ubuntu_image.__main__.main'))
            code = main(('model.assertion',))
        self.assertEqual(code, 0)
        mock.assert_not_called()

    def test_state_machine_exception(self):
        with ExitStack() as resources:
            resources.enter_context(patch(
                'ubuntu_image.__main__.ModelAssertionBuilder',
                CrashingModelAssertionBuilder))
            mock = resources.enter_context(patch(
                'ubuntu_image.__main__._logger.exception'))
            code = main(('model.assertion',))
            self.assertEqual(code, 1)
            self.assertEqual(
                mock.call_args_list[-1], call('Crash in state machine'))


class TestMainWithModel(TestCase):
    def setUp(self):
        super().setUp()
        self._resources = ExitStack()
        self.addCleanup(self._resources.close)
        # Capture builtin print() output.
        self._stdout = StringIO()
        self._stderr = StringIO()
        self._resources.enter_context(
            patch('argparse._sys.stdout', self._stdout))
        # Capture stderr since this is where argparse will spew to.
        self._resources.enter_context(
            patch('argparse._sys.stderr', self._stderr))
        # Set up a few other useful things for these tests.
        self._resources.enter_context(
            patch('ubuntu_image.__main__.logging.basicConfig'))
        self.model_assertion = resource_filename(
            'ubuntu_image.tests.data', 'model.assertion')

    def test_output(self):
        self._resources.enter_context(patch(
            'ubuntu_image.__main__.ModelAssertionBuilder',
            DoNothingBuilder))
        tmpdir = self._resources.enter_context(TemporaryDirectory())
        imgfile = os.path.join(tmpdir, 'my-disk.img')
        self.assertFalse(os.path.exists(imgfile))
        main(('--output', imgfile, self.model_assertion))
        self.assertTrue(os.path.exists(imgfile))

    def test_save_resume_implies_keep(self):
        mock = self._resources.enter_context(patch(
            'ubuntu_image.__main__.ModelAssertionBuilder'))
        self._resources.enter_context(patch(
            'ubuntu_image.__main__.dump'))
        main(('--until', 'make_temporary_directories', self.model_assertion))
        args = mock.call_args[0][0]
        self.assertTrue(args.keep)
        main(('--thru', 'make_temporary_directories', self.model_assertion))
        args = mock.call_args[0][0]
        self.assertTrue(args.keep)

    def test_resume_and_model_assertion(self):
        with self.assertRaises(SystemExit) as cm:
            main(('--resume', self.model_assertion))
        self.assertEqual(cm.exception.code, 2)

    def test_no_resume_and_no_model_assertion(self):
        with self.assertRaises(SystemExit) as cm:
            main(('--until', 'whatever'))
        self.assertEqual(cm.exception.code, 2)

    @skipIf(IN_TRAVIS, 'cannot mount in a docker container')
    def test_save_resume(self):
        self._resources.enter_context(patch(
            'ubuntu_image.__main__.ModelAssertionBuilder',
            XXXModelAssertionBuilder))
        tmpdir = self._resources.enter_context(TemporaryDirectory())
        imgfile = os.path.join(tmpdir, 'my-disk.img')
        main(('--until', 'prepare_filesystems',
              '--channel', 'edge',
              '--output', imgfile,
              self.model_assertion))
        self.assertTrue(os.path.exists(os.path.abspath('.ubuntu-image.pck')))
        self.assertFalse(os.path.exists(imgfile))
        main(('--resume',))
        self.assertTrue(os.path.exists(imgfile))
