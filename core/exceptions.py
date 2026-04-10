# -*- coding: utf-8 -*-
"""Unified exception taxonomy for layered error handling."""


class AppError(Exception):
    """Base class for all application-level errors."""


class CacheIOError(AppError):
    """Local cache read/write/permission errors."""


class NetworkServiceError(AppError):
    """Remote service communication errors."""


class DataFormatError(AppError):
    """Unexpected or invalid data shape/content errors."""


class BusinessRuleError(AppError):
    """Domain/business rule validation errors."""

